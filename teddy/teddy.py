#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
import json
import string
import time
import logging
from decimal import Decimal

from forest.core import (
    Message,
    Response,
    hide,
    requires_admin,
    is_admin,
    run_bot,
)
import forest.utils
from forest.extra import DialogBot
from forest.pdictng import aPersistDict, aPersistDictOfInts, aPersistDictOfLists
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)


class Teddy(DialogBot):
    def __init__(self) -> None:
        self.no_repay: list[str] = []
        # set of valid codes -> "unclaimed" or uuid
        self.valid_codes: aPersistDict[str] = aPersistDict("valid_codes")
        # user -> timestamp in millis of first message
        self.first_messages = aPersistDictOfInts("first_messages")
        # set of known user addresses; user -> address
        self.user_address: aPersistDict[str] = aPersistDict("user_address")
        # set of codes users have claimed; user -> code claimed
        self.user_claimed: aPersistDict[str] = aPersistDict("user_claimed")
        # set of users from whom we are expecting payments
        self.pending_funds: aPersistDict[str] = aPersistDict("pending_funds")
        # configurable map of input - outputs
        self.easter_eggs: aPersistDict[str] = aPersistDict("easter_eggs")
        # record of people we have successfully paid
        self.successful_pays: aPersistDictOfLists[str] = aPersistDictOfLists(
            "successful_pays"
        )
        # key -> int value map
        self.int_map = aPersistDictOfInts("int_map")
        # count of user -> number of eight letter codes entered
        self.attempted_claims = aPersistDictOfInts("attempted_claims")
        # map of balance to distribute
        self.payout_balance_mmob = aPersistDictOfInts("payout_balance_mmob")
        # unused scratchpad for persisting notes and intermediate values
        self.scratch_pad: aPersistDict[str] = aPersistDict("scratch_pad")
        # map users -> sequence of input messages
        self.user_sessions: aPersistDictOfLists[str] = aPersistDictOfLists(
            "user_sessions"
        )
        # global payout lock
        self.pay_lock: asyncio.Lock = asyncio.Lock()
        self.user_state: aPersistDict[str] = aPersistDict("user_state")
        # set of users who opted into followup; user -> timestamp millis
        self.followup_confirmed: aPersistDictOfInts = aPersistDictOfInts(
            "followup_confirmed"
        )
        # okay, this now maps the tag (restore key) of each of the above to the instance of the PersistDict class
        self.state = {
            self.__getattribute__(attr).tag: self.__getattribute__(attr)
            for attr in dir(self)
            if isinstance(self.__getattribute__(attr), aPersistDict)
        }
        super().__init__()

    @requires_admin
    async def do_intset(self, msg: Message) -> Response:
        """Sets intmap values: arg1.upper() to int(arg2)"""
        user = msg.uuid
        if not msg.arg1:
            msg.arg1 = await self.ask_freeform_question(
                user, "What value reference would you like to change?"
            )
        if msg.arg1 and msg.arg2:
            maybe_value = int(msg.arg2)
        if msg.arg1 and not msg.arg2:
            maybe_value = (
                await self.ask_intable_question(
                    user, f"What value to use for {msg.arg1}?"
                )
            ) or 0
        if msg.arg1 and maybe_value:
            await self.int_map.set(msg.arg1, maybe_value or 0)
        return self.int_map.dict_

    @requires_admin
    async def do_dump(self, _: Message) -> Response:
        """dump
        returns a JSON serialization of current state"""
        return json.dumps({k: v.dict_ for (k, v) in self.state.items()}, indent=2)

    async def pay_user_from_balance(
        self, user: str, list_: str, amount_mmob: int
    ) -> Response:
        """Pays a user a given amount of MOB by manually grabbing UTXOs until a transaction can be made."""
        # pylint: disable=too-many-return-statements
        balance = await self.payout_balance_mmob.get(list_, 0)
        # pad fees
        logging.debug(f"PAYING {amount_mmob}mmob from {balance} of {list_}")
        if amount_mmob < balance:
            async with self.pay_lock:
                utxos = list(reversed((await self.mobster.get_utxos()).items()))
                input_pmob_sum = 0
                input_txo_ids = []
                while input_pmob_sum < ((amount_mmob + 1) * 1_000_000_000):
                    txoid, pmob = utxos.pop()
                    if pmob < (amount_mmob * 1_000_000_000) // 15:
                        logging.debug(f"skipping UTXO worth {pmob}pmob")
                        continue
                    input_txo_ids += [txoid]
                    input_pmob_sum += pmob
                    if len(input_txo_ids) > 15:
                        return "Error! Please contact your administrator for support. (too many utxos needed)"
                    logging.debug(
                        f"found: {input_pmob_sum} / {amount_mmob*1_000_000_000} across {len(input_txo_ids)}utxos"
                    )
                if not input_txo_ids:
                    return "Error! Please contact your administrator for support. (not enough utxos)"
                result = await self.send_payment(
                    recipient=user,
                    amount_pmob=(amount_mmob * 1_000_000_000),
                    receipt_message=f'{await self.dialog.get("PAY_MEMO", "PAY_MEMO")}',
                    input_txo_ids=input_txo_ids,
                    confirm_tx_timeout=10,
                )
                if result and result.status == "tx_status_succeeded":
                    await self.payout_balance_mmob.decrement(list_, amount_mmob)
                    await self.successful_pays.extend(f"{list_}_{amount_mmob}", user)
                    return f"Paid you {amount_mmob/1000}MOB"
                return None
        if not balance:
            return f"Error! {list_.title()} has 0mmob balance!"  # thanks y?!
        return "Sorry, can't help you."

    async def do_raisehand(self, msg: Message) -> Response:
        """A user raises their hand which messages their display name to the admins!"""
        user_displayname = await self.get_displayname(msg.uuid)
        nota_bene = msg.text or ""
        if nota_bene:
            nota_bene = f" ({nota_bene})"
        outgoing = f"{user_displayname} just raised their hand ✋{nota_bene}"
        admins = (forest.utils.get_secret("ADMINS") or "").split(",")
        admins += (await self.dialog.get("ADMINS", "")).split(",")
        for admin in admins:
            if admin:
                await self.send_message(admin, outgoing)
        return "Notified the admins!"

    async def do_reset(self, msg: Message) -> Response:
        """Resets a user's attempts and claim code."""
        if not (is_admin(msg) or msg.uuid in await self.dialog.get("ADMINS", "")):
            return "Unauthorized, sorry!"
        user = await self.displayname_lookup_cache.get(msg.arg1 or "", msg.uuid)
        await self.attempted_claims.set(user, 0)
        claimed = await self.user_claimed.get(user)
        if claimed:
            await self.send_message(user, f"Found a code you claimed: {claimed}")
            await self.valid_codes.set(claimed, "unclaimed")
            await self.user_claimed.remove(user)
            await self.user_state.set(user, "FIRST_GREETING")
        await self.first_messages.remove(user)
        await self.send_message(
            user, "Your state has been reset! You may now try again."
        )
        return "Reset!"

    async def wait_then_followup(
        self, msg: Message, timeout_seconds: int = 300
    ) -> Response:
        user = msg.uuid
        await asyncio.sleep(timeout_seconds)
        if await self.ask_yesno_question(
            user, await self.dialog.get("MAY_WE_DM_U", "MAY_WE_DM_U")
        ):
            await self.followup_confirmed.set(user, int(time.time() * 1000))
            return await self.dialog.get("OKAY_WE_WILL_DM_U", "OKAY_WE_WILL_DM_U")
        return await self.dialog.get("TY_WE_WONT_DM_U", "TY_WE_WONT_DM_U")

    async def maybe_claim(self, msg: Message) -> Response:
        """Possibly unlocks a payment."""
        # pylint: disable=too-many-return-statements
        user = msg.uuid
        code = (
            msg.full_text.lower()
            .strip(string.punctuation)
            .replace(" ", "")
            .replace("-", "")
        )
        allowed_claims = await self.int_map.get("allowed_claims", 3)
        attempt_count = await self.attempted_claims.get(user, 0)
        claims_left = allowed_claims - attempt_count - 1
        if user in await self.user_claimed.keys():
            await self.send_message(
                user,
                await self.dialog.get(
                    "USER_ALREADY_CLAIMED_OTHER", "USER_ALREADY_CLAIMED_OTHER"
                ),
            )
            await asyncio.sleep(1)
            await self.send_message(
                user, await self.dialog.get("CHARITIES_INFO", "CHARITIES_INFO")
            )
            return None
        user_address = await self.user_address.get(
            user, ""
        ) or await self.get_signalpay_address(user)
        if not user_address:
            text_message = await self.dialog.get("PLEASE_ACTIVATE", "PLEASE_ACTIVATE")
            await self.send_message(
                user, text_message, attachments=["./how-to-activate.gif"]
            )
            return None
        if user_address and user not in (await self.user_address.keys()):
            await self.user_address.set(user, user_address)
        # TODO: establish support path
        if claims_left < 0:
            return await self.dialog.get("TOO_MANY_ATTEMPTS", "TOO_MANY_ATTEMPTS")
        if len(code) != 8:
            return await self.dialog.get("NOT_8", "NOT_8")
        # if the provided code is in the set of valid codes and is unclaimed...
        if (
            code in await self.valid_codes.keys()
            and await self.valid_codes.get(code, "") == "unclaimed"
        ):
            await self.attempted_claims.increment(user, 1)
            await self.valid_codes.set(code, user)
            await self.user_claimed.set(user, code)
            await self.user_state.set(user, "VALID_CODE_NOW_SENDING")
            await self.send_message(
                user,
                await self.dialog.get(
                    "VALID_CODE_NOW_SENDING", "VALID_CODE_NOW_SENDING"
                ),
            )
            # retry send up to 3x for overkill
            for _ in range(3):
                await self.send_typing(msg)
                payment_amount = await self.int_map.get("amount", 6000)
                payment_result = await self.pay_user_from_balance(
                    user, "teddy", payment_amount
                )
                if payment_result and "Error" not in payment_result:
                    await self.send_message(
                        user,
                        await self.dialog.get("JUST_SENT_PAYMENT", "JUST_SENT_PAYMENT"),
                    )
                    await self.send_typing(msg, stop=True)
                    await self.user_state.set(user, "JUST_SENT_PAYMENT")
                    break
                await self.send_message(
                    user,
                    await self.dialog.get("TRYING_SEND_AGAIN", "TRYING_SEND_AGAIN"),
                )
            # should never fall through
            if not payment_result or payment_result and "Error" in payment_result:
                msg.text = "We just exhausted retries trying to send a payment. Call eng lead!! (Out of balance?)"
                await self.do_raisehand(msg)
                await self.send_message(
                    user, await self.dialog.get("WE_ARE_SO_SORRY", "WE_ARE_SO_SORRY")
                )
                await self.user_state.set(user, "WE_ARE_SO_SORRY")
                await self.valid_codes.set(code, "unclaimed")
                return None
            await asyncio.sleep(1)
            await self.send_message(
                user, await self.dialog.get("CHARITIES_INFO", "CHARITIES_INFO")
            )
            await self.user_state.set(user, "CHARITIES_INFO")
            return await self.wait_then_followup(msg)
        if (
            code in await self.valid_codes.keys()
            and await self.valid_codes.get(code, "") != "unclaimed"
        ):
            await self.attempted_claims.increment(user, 1)
            return await self.dialog.get("CODE_ALREADY_CLAIMED", "CODE_ALREADY_CLAIMED")
        await self.attempted_claims.increment(user, 1)
        if claims_left == 0:
            await self.user_state.set(user, "YOU_ARE_NOW_LOCKED")
            return await self.dialog.get("YOU_ARE_NOW_LOCKED", "YOU_ARE_NOW_LOCKED")
        if claims_left == 1:
            return await self.dialog.get("LAST_TRY", "LAST_TRY")
        return (
            await self.dialog.get(
                "INVALID_CODE_LIMITED_TRIES", "INVALID_CODE_LIMITED_TRIES"
            )
        ).replace("ATTEMPTS_REMAINING", str(claims_left))

    async def handle_message(self, message: Message) -> Response:
        """Method dispatch to do_x commands and goodies.
        Overwrite this to add your own non-command logic,
        but call super().handle_message(message) at the end"""
        if message.full_text and message.uuid not in await self.first_messages.keys():
            await self.first_messages.set(message.uuid, int(time.time() * 1000))
            await self.send_message(
                message.uuid, await self.dialog.get("FIRST_GREETING", "FIRST_GREETING")
            )
        if message.full_text:
            await self.user_sessions.extend(message.uuid, message.full_text)
        return await super().handle_message(message)

    @hide
    async def do_fund(self, msg: Message) -> Response:
        """Allows an owner to add funds for distribution to a list or event."""
        obj = "teddy"
        user = msg.uuid
        await self.pending_funds.set(user, obj)
        self.no_repay += [user]
        return "Okay, waiting for your funds."

    async def do_help(self, msg: Message) -> str:
        """Reminds the user of what we're expecting, then returns a link to the support channel."""
        user = msg.uuid
        user_state = await self.user_state.get(user, "USER_STATE")
        dialog_for_state = await self.dialog.get(user_state)
        help_dialog = await self.dialog.get("HELP", "HELP")
        return help_dialog.replace("USER_STATE", dialog_for_state or "", 1)

    async def default(self, message: Message) -> Response:
        msg = message
        code = msg.arg0
        if not code:
            return None
        if code == "?":
            return await self.do_help(msg)
        if msg.full_text and msg.full_text in [
            key.lower() for key in await self.easter_eggs.keys()
        ]:
            return await self.easter_eggs.get(msg.full_text.lower())
        if code in await self.easter_eggs.keys():
            return await self.easter_eggs.get(code)
        await self.talkback(msg)
        return await self.maybe_claim(msg)
        # handle default case
        # return await self.do_help(msg)

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        # pylint: disable=too-many-return-statements
        amount_mob = float(pmob2mob(amount_pmob).quantize(Decimal("1.0000")))
        amount_mmob = int(amount_mob * 1000)
        maybe_code = await self.pending_funds.pop(msg.uuid)
        if maybe_code:
            code = maybe_code
            await self.payout_balance_mmob.increment(code, amount_mmob)
            if msg.uuid in self.no_repay:
                self.no_repay.remove(msg.uuid)
            return f"We have credited your event {code} {amount_mob}MOB!\n"
        return None


if __name__ == "__main__":
    run_bot(Teddy)
