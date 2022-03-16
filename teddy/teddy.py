#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
import json
import string
import time
import math
import logging
from decimal import Decimal
from typing import Optional

from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time as time_
from prometheus_client import Summary

from forest import utils
from forest.core import (
    Message,
    QuestionBot,
    Response,
    app,
    hide,
    requires_admin,
    is_admin,
    get_uid,
)
from forest.pdictng import aPersistDict, aPersistDictOfInts, aPersistDictOfLists
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class TalkBack(QuestionBot):
    def __init__(self) -> None:
        self.profile_cache: aPersistDict[dict[str, str]] = aPersistDict("profile_cache")
        self.displayname_cache: aPersistDict[str] = aPersistDict("displayname_cache")
        self.displayname_lookup_cache: aPersistDict[str] = aPersistDict(
            "displayname_lookup_cache"
        )
        super().__init__()

    @requires_admin
    async def do_send(self, msg: Message) -> Response:
        """Send <recipient> <message>
        Sends a message as MOBot."""
        obj = msg.arg1
        param = msg.arg2
        if not is_admin(msg):
            await self.send_message(
                utils.get_secret("ADMIN"), f"Someone just used send:\n {msg}"
            )
        if obj and param:
            if obj in await self.displayname_lookup_cache.keys():
                obj = await self.displayname_lookup_cache.get(obj)
            try:
                result = await self.send_message(obj, param)
                return result
            except Exception as err:  # pylint: disable=broad-except
                return str(err)
        if not obj:
            msg.arg1 = await self.ask_freeform_question(
                msg.uuid, "Who would you like to message?"
            )
        if param and param.strip(string.punctuation).isalnum():
            param = (
                (msg.full_text or "")
                .lstrip("/")
                .replace(f"send {msg.arg1} ", "", 1)
                .replace(f"Send {msg.arg1} ", "", 1)
            )  # thanks mikey :)
        if not param:
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid, "What would you like to say?"
            )
        return await self.do_send(msg)

    async def get_displayname(self, uuid: str) -> str:
        """Retrieves a display name from a UUID, stores in the cache, handles error conditions."""
        uuid = uuid.strip("\u2068\u2069")
        # displayname provided, not uuid or phone
        if uuid.count("-") != 4 and not uuid.startswith("+"):
            uuid = await self.displayname_lookup_cache.get(uuid, uuid)
        # phone number, not uuid provided
        if uuid.startswith("+"):
            uuid = self.get_uuid_by_phone(uuid) or uuid
        maybe_displayname = await self.displayname_cache.get(uuid)
        if maybe_displayname:
            return maybe_displayname
        maybe_user_profile = await self.profile_cache.get(uuid)
        # if no luck, but we have a valid uuid
        user_given = ""
        if not maybe_user_profile and uuid.count("-") == 4:
            try:
                maybe_user_profile = (
                    await self.signal_rpc_request("getprofile", peer_name=uuid)
                ).blob or {}
                user_given = maybe_user_profile.get("givenName", "")
                await self.profile_cache.set(uuid, maybe_user_profile)
            except AttributeError:
                # this returns a Dict containing an error key
                user_given = "[error]"
        elif maybe_user_profile and "givenName" in maybe_user_profile:
            user_given = maybe_user_profile["givenName"]
        if not user_given:
            user_given = "givenName"
        if uuid and ("+" not in uuid and "-" in uuid):
            user_short = f"{user_given}_{uuid.split('-')[1]}"
        else:
            user_short = user_given + uuid
        await self.displayname_cache.set(uuid, user_short)
        await self.displayname_lookup_cache.set(user_short, uuid)
        return user_short

    async def talkback(self, msg: Message) -> Response:
        source = msg.uuid or msg.source
        await self.admin(f"{await self.get_displayname(source)} says: {msg.full_text}")
        return None


class Teddy(TalkBack):
    def __init__(self) -> None:
        self.no_repay: list[str] = []
        self.valid_codes: aPersistDict[str] = aPersistDict("valid_codes")
        self.first_messages = aPersistDictOfInts("first_messages")
        self.dialog: aPersistDict[str] = aPersistDict("dialog")
        self.user_claimed: aPersistDict[str] = aPersistDict("user_claimed")
        self.pending_funds: aPersistDict[str] = aPersistDict("pending_funds")
        self.easter_eggs: aPersistDict[str] = aPersistDict("easter_eggs")
        self.successful_pays: aPersistDictOfLists[str] = aPersistDictOfLists(
            "successful_pays"
        )
        self.attempted_claims = aPersistDictOfInts("attempted_claims")
        self.payout_balance_mmob = aPersistDictOfInts("payout_balance_mmob")
        self.challenging: aPersistDict[bool] = aPersistDict("challenging")
        self.scratch_pad: aPersistDict[str] = aPersistDict("scratch_pad")
        self.user_sessions = aPersistDictOfLists("user_sessions")
        self.pay_lock: asyncio.Lock = asyncio.Lock()
        # okay, this now maps the tag (restore key) of each of the above to the instance of the PersistDict class
        self.state = {
            self.__getattribute__(attr).tag: self.__getattribute__(attr)
            for attr in dir(self)
            if isinstance(self.__getattribute__(attr), aPersistDict)
        }
        super().__init__()

    @requires_admin
    async def do_dump(self, msg: Message) -> Response:
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
                )
                if result and not result.status == "tx_status_failed":
                    await self.payout_balance_mmob.decrement(list_, amount_mmob)
                    await self.successful_pays.extend(f"teddy_4", user)
                    return f"Paid you {amount_mmob/1000}MOB"
                return None
        if not balance:
            return f"Error! {list_.title()} has 0mmob balance!"  # thanks y?!
        return "Sorry, can't help you."

    async def do_set(self, msg: Message) -> Response:
        """Let's do it live.
        Unprivileged editing of dialog blurbs, because lfg."""
        user = msg.uuid
        fragment = await self.ask_freeform_question(
            user, "What fragment would you like to change?"
        )
        if fragment in self.TERMINAL_ANSWERS:
            return "OK, nvm"
        blurb = await self.ask_freeform_question(
            user, "What dialog would you like to use?"
        )
        if fragment in self.TERMINAL_ANSWERS:
            return "OK, nvm"
        if old_blurb := await self.dialog.get(fragment):
            await self.send_message(user, "overwriting:")
            await self.send_message(user, old_blurb)
        await self.dialog.set(fragment, blurb)
        # elif not self.is_admin(msg):
        #    return "You must be an administrator to overwrite someone else's blurb!"
        return "updated blurb!"

    async def do_dialog(self, msg: Message) -> Response:
        return "\n\n".join(
            [f"{k}: {v}\n------\n" for (k, v) in self.dialog.dict_.items()]
        )

    async def do_reset(self, msg: Message) -> Response:
        user = msg.uuid
        await self.attempted_claims.set(user, 0)
        claimed = await self.user_claimed.get(user)
        if claimed:
            await self.send_message(user, f"Found a code you claimed: {claimed}")
        await self.valid_codes.set(claimed, "unclaimed")
        await self.user_claimed.remove(user)
        await self.first_messages.remove(user)
        return (
            "Reset your state! The previously used code, if any, may be redeemed again."
        )

    async def maybe_unlock(self, msg: Message) -> Response:
        """Possibly unlocks a payment."""
        # pylint: disable=too-many-return-statements
        user = msg.uuid
        code = msg.full_text.lower().strip(string.punctuation).replace(" ", "")
        attempt_count = await self.attempted_claims.get(user, 0)
        if user in await self.user_claimed.keys():
            await self.send_message(
                user, await self.dialog.get("CHARITIES_INFO", "CHARITIES_INFO")
            )
            if code == await self.user_claimed.get(user):
                return await self.dialog.get(
                    "USER_ALREADY_CLAIMED_THIS", "USER_ALREADY_CLAIMED_THIS"
                )
            return await self.dialog.get(
                "USER_ALREADY_CLAIMED_OTHER", "USER_ALREADY_CLAIMED_OTHER"
            )
        user_address = await self.get_signalpay_address(user)
        if not user_address:
            return await self.dialog.get("PLEASE_ACTIVATE", "PLEASE_ACTIVATE")
        if len(code) != 8:
            return await self.dialog.get("NOT_8", "NOT_8")
        if attempt_count > 2:
            return await self.dialog.get("TOO_MANY_ATTEMPTS", "TOO_MANY_ATTEMPTS")
        # if the provided code is in the set of valid codes and is unclaimed...
        if (
            code in await self.valid_codes.keys()
            and await self.valid_codes.get(code, "") == "unclaimed"
        ):
            await self.attempted_claims.increment(user, 1)
            await self.valid_codes.set(code, user)
            await self.user_claimed.set(user, code)
            payment_result = await self.pay_user_from_balance(user, "teddy", 4)
            if payment_result and "Error" not in payment_result:
                await self.send_message(
                    user,
                    await self.dialog.get("JUST_SENT_PAYMENT", "JUST_SENT_PAYMENT"),
                )
            else:
                return await self.send_message(
                    user, await self.dialog.get("WE_ARE_SO_SORRY", "WE_ARE_SO_SORRY")
                )
            await self.send_message(
                user, await self.dialog.get("CHARITIES_INFO", "CHARITIES_INFO")
            )
            if await self.ask_yesno_question(
                user, await self.dialog.get("MAY_WE_DM_U", "MAY_WE_DM_U")
            ):
                return await self.dialog.get("OKAY_WE_WILL_DM_U", "OKAY_WE_WILL_DM_U")
            else:
                return await self.dialog.get("TY_WE_WONT_DM_U", "TY_WE_WONT_DM_U")
        if (
            code in await self.valid_codes.keys()
            and await self.valid_codes.get(code, "") != "unclaimed"
        ):
            await self.attempted_claims.increment(user, 1)
            return await self.dialog.get("CODE_ALREADY_CLAIMED", "CODE_ALREADY_CLAIMED")
        await self.attempted_claims.increment(user, 1)
        return await self.dialog.get(
            "INVALID_CODE_LIMITED_TRIES", "INVALID_CODE_LIMITED_TRIES"
        )

    async def handle_message(self, message: Message) -> Response:
        """Method dispatch to do_x commands and goodies.
        Overwrite this to add your own non-command logic,
        but call super().handle_message(message) at the end"""
        # try to get a direct match, or a fuzzy match if appropriate
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

    async def default(self, message: Message) -> Response:
        # pylint: disable=too-many-return-statements,too-many-branches
        msg = message
        code = msg.arg0
        if not code:
            return None
        if code == "?":
            return await self.do_help(msg)
        if code == "y":
            return await self.do_yes(msg)
        if code == "n":
            return await self.do_no(msg)
        if (
            code
            and code.rstrip(string.punctuation).lower()
            in "yes yeah ye sure yah".split()  # thanks gang
        ):  # yes!
            return await self.do_yes(msg)
        if msg.full_text and msg.full_text in [
            key.lower() for key in await self.easter_eggs.keys()
        ]:
            return await self.easter_eggs.get(msg.full_text)
        if code in await self.easter_eggs.keys():
            return await self.easter_eggs.get(code)
        await self.talkback(msg)
        maybe_unlocked = await self.maybe_unlock(msg)
        if maybe_unlocked:
            return maybe_unlocked
        # handle default case
        return await self.do_help(msg)

    @time_(REQUEST_TIME)
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
            return (
                f"We have credited your event {code} {amount_mob}MOB!\n"
                + "You may sweep your balance with 'payout' or distrbute specific amounts of millimobb to attendees and individuals with 'pay <user_or_group> <amount> <memo>'."
            )
        return None


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = Teddy()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
