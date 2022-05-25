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

from forest import utils
from forest.core import (
    Message,
    Response,
    hide,
    requires_admin,
    is_admin,
    get_uid,
    run_bot,
)

from forest.extra import DialogBot
from forest.pdictng import aPersistDict, aPersistDictOfInts, aPersistDictOfLists
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)


class Hotline(DialogBot):  # pylint: disable=too-many-public-methods
    def __init__(self) -> None:
        self.no_repay: list[str] = []
        self.pending_orders: aPersistDict[str] = aPersistDict("pending_orders")
        self.pending_funds: aPersistDict[str] = aPersistDict("pending_funds")
        self.pending_donations: aPersistDict[str] = aPersistDict("pending_donations")
        self.event_limits = aPersistDictOfInts("event_limits")
        self.event_prompts: aPersistDict[str] = aPersistDict("event_prompts")
        self.event_prices: aPersistDict[float] = aPersistDict("event_prices")
        # self.event_images: aPersistDict[str] = aPersistDict("event_images")
        self.event_owners: aPersistDictOfLists[str] = aPersistDictOfLists(
            "event_owners"
        )
        self.event_attendees: aPersistDictOfLists[str] = aPersistDictOfLists(
            "event_attendees"
        )
        self.event_lists: aPersistDictOfLists[str] = aPersistDictOfLists("event_lists")
        self.list_owners: aPersistDictOfLists[str] = aPersistDictOfLists("list_owners")
        self.easter_eggs: aPersistDict[str] = aPersistDict("easter_eggs")
        self.successful_pays: aPersistDictOfLists[str] = aPersistDictOfLists(
            "successful_pays"
        )
        self.payout_balance_mmob = aPersistDictOfInts("payout_balance_mmob")
        self.challenging: aPersistDict[bool] = aPersistDict("challenging")
        self.charities: aPersistDict[str] = aPersistDict("charities")
        self.charities_balance_mmob: aPersistDictOfInts = aPersistDictOfInts(
            "charities_balance_mmob"
        )
        self.scratch_pad: aPersistDict[str] = aPersistDict("scratch_pad")
        self.pay_lock: asyncio.Lock = asyncio.Lock()
        self.donations: aPersistDict[str] = aPersistDict("donations")
        self.first_messages: aPersistDict[int] = aPersistDict("first_messages")
        self.last_prompted: aPersistDict[int] = aPersistDict("last_prompted")
        # okay, this now maps the tag (restore key) of each of the above to the instance of the PersistDict class
        self.state = {
            self.__getattribute__(attr).tag: self.__getattribute__(attr)
            for attr in dir(self)
            if isinstance(self.__getattribute__(attr), aPersistDict)
        }
        super().__init__()

    @requires_admin
    async def do_dump(self, msg: Message) -> Response:
        """dump | dump <event>
        returns a JSON serialization of an event (or all events)"""
        obj = (msg.arg1 or "").lower()
        dump = {}
        for eventcode in list(await self.event_owners.keys()) + list(
            await self.list_owners.keys()
        ):
            event = {}
            for parameters in self.state:
                if await self.state[parameters].get(eventcode):
                    event[parameters] = await self.state[parameters].get(eventcode)
            dump[eventcode] = event
        return json.dumps(dump if not obj else dump.get(obj), indent=2)

    async def check_user_owns(self, user_uuid: str, list_name: str) -> Optional[str]:
        """returns 'event' if the user owns the specified event, or 'list' if the user owns the list."""
        if user_uuid in await self.event_owners.get(list_name.lower(), []):
            return "event"
        if user_uuid in await self.list_owners.get(list_name.lower(), []):
            return "list"
        return None

    async def do_check(self, msg: Message) -> Response:
        """check | check <list_or_event>
        returns all lists the user's on, or optionally info about a specified list."""
        obj = (msg.arg1 or "").lower()
        user = msg.uuid or msg.source
        if msg.arg1 and await self.check_user_owns(user, msg.arg1):
            return "\n\n".join(
                [
                    f"code: {obj}",
                    f"prompt: {await self.event_prompts.get(obj)}",
                    f"limit: {await self.event_limits.get(obj)}",
                    f"join price: {await self.event_prices.get(obj, 0)}MOB/ea",
                    f"event owned by: {[await self.get_displayname(uuid) for uuid in await self.event_owners.get(obj, [])]}",
                    f"announce list owned by: {[await self.get_displayname(uuid) for uuid in await self.list_owners.get(obj, [])]}",
                    f"number paid attendees: {len(await self.event_attendees.get(obj, []))}",
                    f"paid attendees: {[await self.get_displayname(uuid) for uuid in await self.event_attendees.get(obj, [])]}",
                    f"list has {len(await self.event_lists.get(obj,[]))} members",
                    f"list members: {[await self.get_displayname(uuid) for uuid in await self.event_lists.get(obj, [])]}",
                    f"CAPTCHA enabled: {await self.challenging.get(obj, False)}",
                    f"balance: {await self.payout_balance_mmob.get(obj, 0)}mmob",
                ]
            )
        lists_ = [
            list_
            for list_ in await self.event_lists.keys()
            if user in await self.event_lists.get(list_, [])
        ]
        owns_event, owns_list = await self._get_user_owns(user)
        return f"You're on the list for {lists_}.\n\nYou own these paid events: {owns_event}\n\nYou own these free lists: {owns_list}\n\nFor more information reply: check <code>."

    async def do_stop(self, msg: Message) -> Response:
        """stop | stop <list>
        Removes user from all lists (optionally, specified list)."""
        removed = 0
        if msg.arg1 and msg.uuid in await self.event_lists.get(
            (msg.arg1 or "").lower(), []
        ):
            await self.event_lists.remove_from((msg.arg1 or "").lower(), msg.uuid)
            return f"Okay, removed you from {msg.arg1}"
        if not msg.arg1:
            for list_ in await self.event_lists.keys():
                if msg.uuid in await self.event_lists.get(list_, []):
                    await self.event_lists.remove_from(list_, msg.uuid)
                    await self.send_message(
                        msg.uuid,
                        f"Removed you from list {list_}, to rejoin send 'subscribe {list_}'",
                    )
                    removed += 1
        if msg.arg1 and not removed:
            return f"Sorry, you're not on the announcement list for {msg.arg1}"  # thanks y?!
        if not removed:
            return "You're not on any lists!"
        return None

    @hide
    async def do_payout(self, msg: Message) -> Response:
        """Sweeps all balance for an event to the requesting owner.
        Prompts admin for approval."""
        # pylint: disable=too-many-return-statements
        user = msg.uuid
        list_ = (msg.arg1 or "").lower()
        user_owns = await self.check_user_owns(user, list_)
        balance = await self.payout_balance_mmob.get(list_, 0)
        if is_admin(msg) or (user_owns and balance):
            if not await self.ask_yesno_question(
                utils.get_secret("ADMIN"),
                f"Owner of {list_} requests payout of {balance}. Approve?",
            ):
                return "Sorry, admin rejected your payout."
            return await self.pay_user_from_balance(user, list_, balance - 1)
        return "Sorry, no luck"

    async def pay_user_from_balance(
        self, user: str, list_: str, amount_mmob: int
    ) -> Optional[str]:
        """Pays a user a given amount of MOB by manually grabbing UTXOs until a transaction can be made.
        Assumptions made:"""
        # pylint: disable=too-many-return-statements,too-many-locals,too-many-branches
        balance = await self.payout_balance_mmob.get(list_, 0)
        await self.send_typing(recipient=user)
        # pad fees
        logging.debug(f"PAYING {amount_mmob}mmob from {balance} of {list_}")
        if amount_mmob < balance:
            async with self.pay_lock:
                utxos = list(reversed((await self.mobster.get_utxos()).items()))
                input_pmob_sum = 0
                input_txo_ids = []
                # dust
                skipped_utxos = []
                # acquire utxos
                while input_pmob_sum < ((amount_mmob + 1) * 1_000_000_000):
                    if utxos:
                        # get a txoid and amount to check
                        txoid, pmob = utxos.pop()
                    else:
                        # no utxos left, we've reviewed all of the available ones.
                        # release so the recursively instantiated children calls can re-acquire
                        self.pay_lock.release()
                        # recurse with smaller amounts
                        first_half = await self.pay_user_from_balance(
                            user, list_, amount_mmob // 2
                        )
                        # if first leg succeeds..
                        if first_half and "Paid" in first_half:
                            # let's do it again
                            second_half = await self.pay_user_from_balance(
                                user, list_, amount_mmob // 2
                            )
                            # and if we're winning
                            if second_half and "Paid" in second_half:
                                # acquire to exit-handler more nicely
                                await self.pay_lock.acquire()
                                return f"Paid you you {amount_mmob/1000}MOB"
                        else:
                            # sadly acquire lock in defeat
                            await self.pay_lock.acquire()
                        return None
                    if pmob < (amount_mmob * 1_000_000_000) // 15:
                        logging.debug(f"skipping UTXO worth {pmob}pmob")
                        skipped_utxos += [(txoid, pmob)]
                        continue
                    input_txo_ids += [txoid]
                    input_pmob_sum += pmob
                    if len(input_txo_ids) > 15:
                        return "Something went wrong! Please contact your administrator for support. (too many utxos needed)"
                # how many slots do we have for dust?
                dust_space = 16 - len(input_txo_ids)
                # grab dust up to 16 utxos total or up to # dust, whichever is smaller
                logging.info(
                    f"Space for {dust_space}, we have {len(skipped_utxos)} presumed dust!"
                )
                for _ in range(min(dust_space, len(skipped_utxos))):
                    # smallest dust = higher priority for cleaning
                    dust_txoid, dust_val_pmob = skipped_utxos.pop(0)
                    input_txo_ids.append(dust_txoid)
                    logging.debug(
                        f"grabbing dust worth {dust_val_pmob}pmob to fill empty space in transaction inputs"
                    )
                    input_pmob_sum += dust_val_pmob
                logging.debug(
                    f"found: {input_pmob_sum} / {amount_mmob*1_000_000_000} across {len(input_txo_ids)}utxos"
                )
                if not input_txo_ids:
                    return "Something went wrong! Please contact your administrator for support. (not enough utxos)"
                # build a memo lookup key for the relevant list
                MEMO_KEY = "PAY_MEMO_" + list_
                # attempt to fetch PAY_MEMO_list_ falling back to DEFAULT_PAY_MEMO
                memo_dialog = await self.dialog.get(
                    MEMO_KEY, None
                ) or await self.dialog.get("DEFAULT_PAY_MEMO", "DEFAULT_PAY_MEMO")
                result = await self.send_payment(
                    recipient=user,
                    amount_pmob=(amount_mmob * 1_000_000_000),
                    receipt_message=memo_dialog,
                    input_txo_ids=input_txo_ids,
                    confirm_tx_timeout=60,
                )
                await self.send_typing(recipient=user, stop=True)
                if result and result.status == "tx_status_succeeded":
                    await self.payout_balance_mmob.decrement(list_, amount_mmob)
                    return f"Paid you you {amount_mmob/1000}MOB"
                return None
        if not balance:
            return "Sorry, {list_} has 0mmob balance!"  # thanks y?!
        return "Sorry, can't help you."

    @hide
    async def do_pay(self, msg: Message) -> Response:
        """Allows an event/list owner to distribute available funds across those on a list."""
        user = msg.uuid
        if not msg.arg2 or not msg.arg2.isnumeric():
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid,
                "How many mMOB should each recipient recieve (1000mMOB = 1MOB)?",
            )
            if msg.arg2 == "0":
                return "OK, cancelling."
        amount_mmob = 0  # excuse me?
        list_, amount, message = (
            (msg.arg1 or "").lower(),
            (msg.arg2 or "0"),
            msg.arg3 or msg.arg1 or "",
        )
        if not amount.isnumeric() or not amount:
            msg.arg2 = await self.ask_freeform_question(
                user, "Please provide an amount of milliMOB as a number:"
            )
            if msg.arg2 == "0":
                return "OK, cancelling."
            return await self.do_pay(msg)
        amount_mmob = int(amount)
        if not list_:
            msg.arg1 = await self.ask_freeform_question(
                user, "Who would you like to send the mMOB to?"
            )
            return await self.do_pay(msg)
        user_owns = await self.check_user_owns(user, list_)
        if not is_admin(msg) and not user_owns:
            return "Sorry, you are not authorized."
        return await self.pay_list(msg, amount_mmob, list_, message)

    async def pay_list(
        self,
        msg: Message,
        amount_mmob: int,
        list_: str,
        message: str,
    ) -> Response:
        "Actually distribute funds across those on a list." ""
        if not (
            list_ in await self.event_lists.keys()
            or list_ in await self.event_attendees.keys()
        ):
            return "Sorry, that's not a valid list or number!"
        to_send = await self.event_lists.get(
            list_, []
        ) or await self.event_attendees.get(list_, [])
        save_key = f"{list_}_{amount_mmob}_{message}"
        filtered_send_list = [
            user
            for user in to_send
            if user not in await self.successful_pays.get(save_key, [])
        ]
        total_mmob = len(filtered_send_list) * amount_mmob
        if len(to_send) > 0 and len(filtered_send_list) == 0:
            return "Warning: already sent to this combination! Change the memo, amount, or list and retry."
        if not is_admin(msg) and (
            total_mmob > await self.payout_balance_mmob.get(list_, 0)
        ):
            return "Not enough balance remaining on this event!"
        await self.send_message(
            msg.uuid,
            f"about to send {total_mmob}mmob to {len(filtered_send_list)} folks on {list_}",
        )
        await self.send_message(
            msg.uuid,
            f"Using this for the memo:\n\n > {message}",
        )
        if not await self.ask_yesno_question(msg.uuid):
            return "OK, canceling"
        async with self.pay_lock:
            valid_utxos = [
                utxo
                for utxo, upmob in (await self.mobster.get_utxos()).items()
                if upmob > (1_000_000_000 * (amount_mmob + 1))
            ]
            if len(valid_utxos) < len(filtered_send_list):
                await self.send_message(
                    msg.uuid,
                    "Please wait! Insufficient number of utxos!\nBuilding more...",
                )
                building_msg = await self.mobster.split_txos_slow(
                    amount_mmob + 1, (len(filtered_send_list) - len(valid_utxos))
                )
                await self.send_message(msg.uuid, building_msg)
                valid_utxos = [
                    utxo
                    for utxo, upmob in (await self.mobster.get_utxos()).items()
                    if upmob > (1_000_000_000 * (amount_mmob + 1))
                ]
            failed = []

            async def pay_logging_success(
                target: str,
                amount_mmob: int,
                message: str = "",
                input_txo_ids: Optional[list[str]] = None,
            ) -> Optional[Message]:
                if not input_txo_ids:
                    input_txo_ids = []
                try:
                    await self.send_typing(recipient=target)
                    result = await self.send_payment(
                        recipient=target,
                        amount_pmob=amount_mmob * 1_000_000_000,
                        receipt_message=message,
                        input_txo_ids=input_txo_ids,
                        confirm_tx_timeout=60,
                    )
                    await asyncio.sleep(0.5)
                    await self.send_typing(recipient=target, stop=True)
                    # if we didn't get a result indicating success
                    if not result or (
                        result and result.status != "tx_status_succeeded"
                    ):
                        # stash as failed
                        return None
                    # persist user as successfully paid
                    await self.successful_pays.extend(save_key, target)
                    await self.payout_balance_mmob.decrement(list_, amount_mmob)
                    await self.send_message(target, "I've sent you a payment!")
                    return result
                except Exception:  # pylint: disable=broad-except
                    return None

            results = [
                await pay_logging_success(
                    target,
                    amount_mmob,
                    message,
                    input_txo_ids=[valid_utxos.pop(0) or ""],
                )
                for target in filtered_send_list
            ]
            failed = [filtered_send_list[i] for (i, x) in enumerate(results) if not x]
            if len(failed):
                await self.send_message(
                    msg.uuid,
                    (
                        f"failed on\n{[await self.get_displayname(uuid) for uuid in failed]}\n"
                        "Copy and paste your original pay message and resend to retry."
                    ),
                )
            return "completed sends"
        return "failed"

    @hide
    async def do_fund(self, msg: Message) -> Response:
        """Allows an owner to add funds for distribution to a list or event.
        fund <listname>
        fund <eventname>
        """
        obj = (msg.arg1 or "").lower()
        user = msg.uuid
        await self.pending_orders.remove(msg.uuid)
        if await self.check_user_owns(user, obj):
            await self.pending_funds.set(user, obj)
            self.no_repay += [user]
            return "Okay, waiting for your funds."
        return "Sorry, can't find an event by that name."

    @hide
    async def do_give(self, msg: Message) -> Response:
        """Donate to one of the supported charities!
        give <charityname>
        """
        obj = (msg.arg1 or "").lower()
        user = msg.uuid
        await self.pending_donations.remove(msg.uuid)
        give_message = await self.easter_eggs.get("give", "")
        self.no_repay += [user]
        await self.pending_donations.set(user, "standwithukraine")
        await self.send_message(
            user,
            "Charity donations to date!\n\n"
            + "\n".join(
                [
                    f"{k}: {str(v/1000)[:5]}MOB"
                    for (k, v) in self.charities_balance_mmob.dict_.items()
                    if v and isinstance(v, int)
                ]
            ),
        )
        if not obj:
            obj = await self.ask_freeform_question(user, give_message)
            if obj:
                obj = obj.lower()
            else:
                return None
            if obj.isnumeric():
                obj = await self.easter_eggs.get(f"{obj}_give", "standwithukraine")
        if obj in await self.charities.keys():
            await self.pending_donations.set(user, obj)
            charity_info = await self.charities.get(obj, "")
            return f"Okay, waiting for your donation to {await self.easter_eggs.get(obj, obj)}!\n\n{charity_info}\n\nSend me a payment over Signal and I will make sure it gets to them."
        if not obj or obj not in self.TERMINAL_ANSWERS:
            obj = await self.ask_freeform_question(user, give_message)
            if obj.isnumeric():
                obj = await self.easter_eggs.get(f"{obj}_give", "standwithukraine")
            msg.arg1 = obj
            return await self.do_give(msg)
        return "Okay, maybe later!"

    @hide
    async def do_blast(self, msg: Message) -> Response:
        """blast  <listname> "message"
        blast <eventname> "message"
        """
        obj, param = (msg.arg1 or ""), (msg.arg2 or "")
        user = msg.uuid
        sent = []
        success = False
        user_owns = await self.check_user_owns(user, obj)
        if user_owns and param:
            # if the user forgot the quotes, the param will be a single word
            if param.rstrip(string.punctuation).isalnum():
                param = (
                    (msg.full_text or "")
                    .lstrip("/")
                    .replace(f"blast {msg.arg1} ", "", 1)
                    .replace(f"Blast {msg.arg1} ", "", 1)
                )  # thanks mikey :)
            success = True
            target_admins = list(
                set(
                    await self.event_owners.get(obj.lower(), [])
                    + await self.list_owners.get(obj.lower(), [])
                )
            )
            target_users = list(
                set(
                    await self.event_lists.get(obj.lower(), [])
                    + await self.event_attendees.get(obj.lower(), [])
                    + target_admins
                )
            )
            # send preview
            await self.send_message(msg.uuid, param)
            # ask for confirmation
            if not await self.ask_yesno_question(
                msg.uuid,
                f"Would you like to blast the above message (as written) to {len(target_users)}? (yes/no)",
            ):
                return "ok, let's not."
            # do the blast
            for target_user in target_users:
                if target_user in target_admins:
                    await self.send_message(
                        target_user.strip("\u2068\u2069"),
                        param + f"\n - {await self.get_displayname(msg.uuid)}",
                    )
                else:
                    await self.send_message(target_user.strip("\u2068\u2069"), param)
                sent.append(target_user)
                await asyncio.sleep(0.01)
        elif user_owns:
            msg.full_text = msg.arg2 = param = await self.ask_freeform_question(
                msg.uuid, "What would you like to send?"
            )
            return await self.do_blast(msg)
        if not success:
            return "That didn't work! Try 'blast <list code> 'mymessage'. You can only send to lists you own!"
        # confirm we finished
        return f"Finished sending to {len(sent)} recipients on the {obj} list"

    @hide
    async def do_subscribe(self, msg: Message) -> Response:
        """Subscribe to a list."""
        obj = (msg.arg1 or "").lower()
        if obj not in await self.event_lists.keys():
            return f"Sorry, I couldn't find a list called {obj} - to create your own, try 'add list {obj}'."
        if msg.uuid in await self.event_lists[obj]:
            return f"You're already on the {obj} list!"
        await self.event_lists.extend(obj, msg.uuid)
        return f"Added you to the {obj} list!"

    async def do_help(self, msg: Message) -> Response:
        if msg.uuid not in await self.first_messages.keys():
            await self.first_messages.set(msg.uuid, int(time.time() * 1000))
            if await self.dialog.get("FIRST_GREETING"):
                return await self.dialog.get("FIRST_GREETING", "FIRST_GREETING")
        return await self.dialog.get("WELCOME", "WELCOME")

    @hide
    async def do_remove(self, msg: Message) -> Response:
        """Removes a given event by code, if the msg.uuid is the owner."""
        if not await self.check_user_owns(msg.uuid, msg.arg1 or ""):
            return f"Sorry, it doesn't look like you own {msg.arg1}."
        parameters = []
        for state_ in self.state.keys():
            if "egg" not in state_ and msg.arg1 in (await self.state[state_].keys()):
                parameters += [state_]
        lists = []

        # show human-friendly version before prompting removal
        await self.send_message(
            msg.uuid, "This event currently has the following state:"
        )
        await self.send_message(msg.uuid, await self.do_check(msg))
        if await self.ask_yesno_question(
            msg.uuid, f"Are you sure you want to remove {msg.arg1}?"
        ):
            for state_ in self.state.keys():
                if "egg" not in state_ and msg.arg1 in (
                    await self.__getattribute__(state_).keys()
                ):
                    lists += [state_]
                    await self.__getattribute__(state_).remove(msg.arg1)
            return f"Okay, removed {msg.arg1} {lists}"
        return "Okay, not removing"

    @hide
    async def do_set(self, msg: Message) -> Response:
        """Set is an alias for add"""
        return await self.do_add(msg)

    async def do_setup(self, msg: Message) -> Response:
        "A question-and-answer based workflow for setting up events and lists"
        if not msg.arg1:
            msg.arg1 = await self.ask_freeform_question(
                msg.uuid, "What event or list would you like to setup?"
            )
        obj = (msg.arg1 or "").lower()
        user = msg.uuid
        event_or_list = await self.check_user_owns(msg.uuid, obj)
        if not event_or_list:
            return "Please try again with a list or event that you own!"
        if await self.ask_yesno_question(
            user,
            f"Would you like to limit the number of individuals who may join this {event_or_list}?",
        ):
            msg.arg1 = "limit"
            msg.arg2 = obj
            msg.arg3 = await self.ask_freeform_question(
                user, "What limit would you like to set?"
            )
            await self.send_message(user, await self.do_add(msg))
        if event_or_list == "event" and await self.ask_yesno_question(
            user,
            f"Would you like to change the price from {await self.event_prices.get(obj, 0)}MOB?",
        ):
            msg.arg1 = "price"
            msg.arg2 = obj
            msg.arg3 = await self.ask_freeform_question(
                user, f"What price would you like to set for the {obj} event?"
            )
            await self.send_message(user, await self.do_add(msg))
        if event_or_list and await self.ask_yesno_question(
            user,
            f"Would you like to set a prompt for {event_or_list} {obj}?\n\nIt is currently: \n'{await self.event_prompts.get(obj)}'",
        ):

            msg.arg1 = "prompt"
            msg.arg2 = obj
            msg.arg3 = await self.ask_freeform_question(
                user,
                f"What prompt would you like to set for the {obj} {event_or_list}?",
            )
            await self.send_message(user, await self.do_add(msg))
        msg.arg1 = obj
        return await self.do_check(msg)

    @hide
    async def do_add(self, msg: Message) -> Response:
        """add event <eventcode>
        > add event TEAMNYE22
        Okay, you're now the proud owner of an event on The Hotline, secret code TEAMNYE22!
        > add owner TEAMNYE22 +1-555-000-1234
        Okay, +15550001234 has been notified that they are owners of this event.
        They can also edit details, and will be notified of sales.
        > add price TEAMNYE22 0
        > add prompt TEAMNYE22 "the gang celebrates 2023 with a cool new years eve party. yeah, we plan ahead!"
        > add limit TEAMNYE22 200
        > add list COWORKERS
        """
        # pylint: disable=too-many-return-statements,too-many-branches,too-many-statements
        if not msg.arg1:
            msg.arg1 = await self.ask_freeform_question(
                msg.uuid, "Would you like to add an event, easteregg, or a list?"
            )
        obj, param, value = (
            (msg.arg1 or "").lower(),
            (msg.arg2 or "").lower(),
            msg.arg3 or "",
        )
        value = value.strip("\u2068\u2069")
        user = msg.uuid or msg.source  # should always be uuid, be nice to types tho
        user_owns = await self.check_user_owns(user, param)
        success = False
        if (
            obj in "egg easteregg"
            and not await self.easter_eggs.get(param, None)
            or await self.get_displayname(msg.uuid)
            in await self.easter_eggs.get(param, "")
            or msg.uuid in await self.easter_eggs.get(param, "")
            or msg.source in await self.easter_eggs.get(param, "")
        ):
            if not param:
                param = await self.ask_freeform_question(
                    user, "What word or phrase would you like to show the easter egg?"
                )
            maybe_old_message = await self.easter_eggs.get(param, "")
            if not value and not maybe_old_message:
                value = await self.ask_freeform_question(
                    user,
                    "What phrase should be returned when the easter egg is revealed?",
                )
            if maybe_old_message:
                await self.send_message(msg.uuid, f"replacing: {maybe_old_message}")
                await self.easter_eggs.set(
                    param,
                    f"{value} - updated by {await self.get_displayname(msg.uuid)}",
                )
                return f"Updated {param} to read {value}"
            await self.easter_eggs.set(
                param.lower(),
                f"{value} - added by {await self.get_displayname(msg.uuid)}",
            )
            return f'Added an egg! "{param}" now returns\n > {value} - added by {await self.get_displayname(msg.uuid)}'
        if obj == "egg" and param in await self.easter_eggs.keys():
            return f"Sorry, egg already has value {await self.easter_eggs.get(param)}. Please message support to change it."
        if (
            obj == "event"
            and param
            and param not in await self.event_owners.keys()
            and param not in await self.list_owners.keys()
        ):
            await self.event_owners.set(param, [user])
            await self.list_owners.set(param, [user])
            await self.event_attendees.set(param, [])
            await self.event_lists.set(param, [])
            # await self.event_images.set(param, [])
            await self.event_prompts.set(param, "")
            await self.payout_balance_mmob.set(param, 0)
            if await self.ask_yesno_question(
                user, f"Would you like to setup your new event '{param}' now? (yes/no)"
            ):
                msg.arg1 = param
                return await self.do_setup(msg)
            return f'You now own paid event "{param}", and a free list by the same name - use "setup {param}" to configure your event at your convenience!'
        if (
            obj == "list"
            and param
            and param not in await self.list_owners.keys()
            and param not in await self.event_owners.keys()
        ):
            await self.list_owners.set(param, [user])
            await self.event_lists.set(param, [])
            # await self.event_images.set(param, [])
            await self.event_prompts.set(param, "")
            if await self.ask_yesno_question(
                user, f"Would you like to setup your new list '{param}' now? (yes/no)"
            ):
                msg.arg1 = param
                msg.arg2 = ""
                msg.arg3 = ""
                return await self.do_setup(msg)
            return f'You now own a free announcement list named {param} - use "setup {param}" to configure your event at your convenience!'
        if obj == "event" and not param:
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid, "What unlock code would you like to use for this event?"
            )
            return await self.do_add(msg)
        if obj == "list" and not param:
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid, "What unlock code would you like to use for this list?"
            )
            return await self.do_add(msg)
        if user_owns and not value:
            return await self.do_setup(msg)

        # if the user owns the event and we have a value passed
        if user_owns and value:
            if obj == "owner":
                if value in await self.displayname_lookup_cache.keys():
                    new_owner_uuid = await self.displayname_lookup_cache.get(
                        value, value
                    )
                elif value in await self.displayname_cache.keys():
                    new_owner_uuid = value
                if user_owns == "event":
                    if value not in await self.event_owners.get(param, []):
                        await self.event_owners.extend(param, new_owner_uuid)
                if user_owns == "list":
                    if value not in await self.list_owners.get(param, []):
                        await self.list_owners.extend(param, new_owner_uuid)
                if user_owns:
                    await self.send_message(
                        new_owner_uuid,
                        f"You've been added as an owner of the {param} {user_owns} by {await self.displayname_cache.get(msg.uuid)}!",
                    )
                success = True
            elif obj == "price" and user_owns == "event":
                # check if string == floatable
                if (
                    value.replace("-", "1", 1).replace(".", "1", 1).isnumeric()  # 1.01
                    or value.replace("-", "1", 1)
                    .replace(",", "1", 1)
                    .isnumeric()  # 1,01
                ):
                    await self.event_prices.set(
                        param, float(value.replace(",", ".", 1))
                    )  # eu standard decimal as 1,00 h/t y?!
                    success = True
                else:
                    msg.arg3 = await self.ask_freeform_question(
                        msg.uuid,
                        "I didn't understand that, what price would you like to set? (as a number)",
                    )
                    return await self.do_add(msg)
            elif obj == "prompt":
                # todo add validation
                await self.event_prompts.set(param, value)
                success = True
            elif obj == "limit":
                # check if int
                if value.isnumeric():
                    await self.event_limits.set(param, int(value))
                    success = True
                else:
                    msg.arg3 = await self.ask_freeform_question(
                        msg.uuid,
                        "I didn't understand that, what limit would you like to set? (as a number)",
                    )
                    return await self.do_add(msg)
        if success:
            return f"Successfully added '{value}' to event {param}'s {obj}!"
        return f"Failed to add {value} to event {param}'s {obj}!"

    async def maybe_unlock(self, msg: Message) -> Response:
        """Possibly unlocks an event."""
        # pylint: disable=too-many-return-statements,too-many-branches
        code = msg.arg0.strip(string.punctuation)
        # if the event has an owner and a price and there's attendee space and the user hasn't already bought tickets
        if (
            code
            and code in await self.event_owners.keys()  # event has an owner
            and code in await self.event_prices.keys()  # and a price
            and (
                len(await self.event_attendees.get(code, []))
                < await self.event_limits.get(code, 1000)
            )  # and there's space
            and msg.uuid not in await self.event_attendees.get(code, [])
        ):
            if await self.event_prices.get(code, 0) > 0:
                self.pending_orders[msg.uuid] = code
                return [
                    await self.event_prompts.get(code) or "Event Unlocked!",
                    f"You may now make one purchase of up to 2 tickets at {await self.event_prices[code]} MOB ea.\nIf you have payments activated, open the conversation on your Signal mobile app, click on the plus (+) sign and choose payment.",
                ]
            if await self.event_prices.get(code, 0) < 0:
                if not await self.get_signalpay_address(msg.uuid):
                    return await self.dialog.get("PLEASE_ACTIVATE", "PLEASE_ACTIVATE")
                await self.send_message(
                    msg.uuid,
                    await self.dialog.get("ABOUT_TO_PAY", "Sending a payment!"),
                )
                await self.event_attendees.extend(code, msg.uuid)
                res = await self.pay_user_from_balance(
                    msg.uuid,
                    code,
                    math.ceil(-1000 * await self.event_prices.get(code, 0)),
                )
                if res and "Paid" in res:
                    return await self.event_prompts.get(code, res)
                await self.event_attendees.remove_from(code, msg.uuid)
                return await self.dialog.get("WE_ARE_SO_SORRY", "Try again!")
            await self.send_message(
                msg.uuid,
                f"{await self.event_prompts.get(code) or 'You have unlocked an event!'}",
            )
            if await self.ask_yesno_question(
                msg.uuid, "Would you like to bring a guest?"
            ):
                await self.event_attendees.extend(code, msg.uuid)
            await self.event_attendees.extend(code, msg.uuid)
            return f"You're on the list for {code}!"
        # if there's a list but no attendees
        if (
            code  # if there's a code and...
            and code in await self.event_lists.keys()  # if there's a list and...
            and not await self.event_prices.get(code, 0)  # and it's free
        ):
            if len(await self.event_lists.get(code, [])) > await self.event_limits.get(
                code, 1000
            ):
                return f"Sorry, {code} is full!"
            if msg.uuid in await self.event_lists.get(code, []):
                return f"You're already on the {code} list!"
            if await self.challenging.get(code):
                await self.send_message(
                    msg.uuid, "Before you continue, you need to solve a challenge!"
                )
                await self.do_challenge(msg)
                if not await self.ask_yesno_question(
                    msg.uuid,
                    f"Thank you for helping keep our community safe!\n\nYou've unlocked {code}, would you like to be added to the list?",
                    require_first_device=True,
                ):
                    return f"Okay, but you're missing out! \n\nIf you change your mind, unlock the list again by sending '{code}'"
            else:
                if not await self.ask_yesno_question(
                    msg.uuid,
                    f"You've unlocked {code}! Would you like to be added to the list?",
                ):
                    return f"Okay, but you're missing out! \n\nIf you change your mind, unlock the list again by sending '{code}'"
            if await self.event_prompts.get(code):
                await self.send_message(msg.uuid, await self.event_prompts.get(code))
            await self.event_lists.extend(code, msg.uuid)
            return f"Added you to the {code} list!"
        if (
            code
            and code in await self.event_owners.keys()
            and code  # event has owner
            in await self.event_prices.keys()  # event has price (not just a stand-alone list)
            and code in await self.event_lists.keys()  # if there's a list and...
            and msg.uuid in await self.event_attendees[code]  # user on the list
        ):
            return await self.dialog.get(
                "ALREADY_JOINED_" + code,
                "You're already on the list!",
            )
        return None

    async def _get_user_lists_and_admins(
        self, msg: Message
    ) -> tuple[list[str], list[str]]:
        """Takes a message, returns all lists and all list owners related to the originating user."""
        lists = []
        all_owners = []
        for list_ in await self.event_lists.keys():
            # if user is on a list
            if msg.uuid in await self.event_lists.get(list_, []):
                owners = await self.event_owners.get(list_, [])
                owners += await self.list_owners.get(list_, [])
                all_owners += owners
                lists += [list_]
            # if user bought tickets
            if (
                list_ in await self.event_attendees.keys()
                and msg.uuid in await self.event_attendees.get(list_, [])
            ):
                owners = await self.event_owners.get(list_, [])
                all_owners += owners
                lists += [list_]
            # if user has started buying tickets
            maybe_pending = await self.pending_orders.get(msg.uuid)
            if maybe_pending and maybe_pending in await self.event_owners.keys():
                all_owners += await self.event_owners.get(maybe_pending, [])
                lists += [f"pending: {maybe_pending}"]
        return lists, list(set(all_owners))

    async def _get_user_owns(self, user: str) -> tuple[list[str], list[str]]:
        """Returns a 2-tuple of all events and lists owned by a user (passed as uuid)."""
        owns_event = [
            list_
            for list_ in await self.event_lists.keys()
            if user in await self.event_owners.get(list_, [])
        ]
        owns_list = [
            list_
            for list_ in await self.event_lists.keys()
            if user in await self.list_owners.get(list_, [])
        ]
        return owns_event, owns_list

    async def do_reset(self, msg: Message) -> Response:
        """reset <displayname>
        Allows the owner of a list or event to reset the session state of an attendee."""
        user = msg.uuid
        owns_event, owns_list = await self._get_user_owns(user)
        if (
            (owns_event or owns_list)
            and msg.arg1
            and (maybe_uuid := await self.displayname_lookup_cache.get(msg.arg1, ""))
        ):
            user_is_admin = is_admin(msg)
            msg.uuid = maybe_uuid
            _, admins = await self._get_user_lists_and_admins(msg)
            if (user in admins) or user_is_admin:
                await self.send_message(maybe_uuid, "TERMINATE", end_session=True)
                await self.send_message(maybe_uuid, "TERMINATE", end_session=True)
                await self.send_message(maybe_uuid, "Reset your session!")
                return f"Reset the session for {msg.arg1}."
            return "You're not administrator of any lists this user is on!"
        if (owns_event or owns_list) and msg.arg1 and msg.arg1.startswith("+"):
            await self.send_message(msg.arg1, "TERMINATE", end_session=True)
            sent_okay = await self.send_message(msg.arg1, "Reset your session!")
            return f"Reset {msg.arg1} and notified: {sent_okay}."
        return "Couldn't find that username!"

    async def talkback(self, msg: Message) -> None:
        """Override talkback implementation to show context about events and notify list/event owners."""
        lists, all_owners = await self._get_user_lists_and_admins(msg)
        for owner in all_owners:
            await self.send_message(
                owner,
                f"{await self.get_displayname(msg.uuid)} ( {msg.source} ) says: {msg.full_text}\nThey are on the following lists: {list(set(lists))}",
            )
            await asyncio.sleep(0.1)
        if not all_owners:
            await super().talkback(msg)
        return None

    async def default(self, message: Message) -> Response:
        # pylint: disable=too-many-return-statements,too-many-branches
        msg = message
        code = msg.arg0
        if not code:
            return None
        if code == "?":
            return await self.do_help(msg)
        if code in "+ buy purchase".split():  # was a function, now helptext
            return await self.dialog.get("HOW_TO_SEND")
        if not code:
            return None
        if msg.full_text and msg.full_text.strip(string.punctuation) in [
            key.lower().strip(string.punctuation)
            for key in await self.easter_eggs.keys()
        ]:
            return await self.easter_eggs.get(msg.full_text)
        if code in await self.easter_eggs.keys():
            return await self.easter_eggs.get(code)
        maybe_unlocked = await self.maybe_unlock(msg)
        if maybe_unlocked:
            return maybe_unlocked
        await self.talkback(msg)
        # handle default case
        if (time.time() * 1000 - await self.last_prompted.get(msg.uuid, 0)) > 60 * 1000:
            await self.last_prompted.set(msg.uuid, int(time.time() * 1000))
            return await self.do_help(msg)
        await self.last_prompted.set(msg.uuid, int(time.time() * 1000))
        return None

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        # pylint: disable=too-many-return-statements
        amount_mob = float(pmob2mob(amount_pmob).quantize(Decimal("1.0000")))
        amount_mmob = int(amount_mob * 1000)
        if msg.uuid in await self.pending_orders.keys():
            code = (await self.pending_orders.get(msg.uuid, "")).lower()
            price = await self.event_prices.get(code, 1000)
            if (
                price
                and amount_mob >= price
                and len(await self.event_attendees.get(code, []))
                < await self.event_limits.get(code, int(1e5))
                and msg.uuid not in (await self.event_attendees.get(code, []))
            ):
                await self.payout_balance_mmob.increment(code, amount_mmob)
                end_note = ""
                if (amount_mob // price) == 2:
                    await self.event_attendees.extend(code, msg.uuid)
                    end_note = "(times two!)"
                await self.event_attendees.extend(code, msg.uuid)
                thank_you = f"Thanks for paying for {await self.pending_orders[msg.uuid]}.\nYou're on the list! {end_note}"
                await self.pending_orders.remove(msg.uuid)
                return thank_you
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
        if msg.uuid in await self.pending_donations.keys():
            code = (await self.pending_donations.get(msg.uuid)) or ""
            donation_uid = get_uid()
            donation_time = time.time()
            await self.donations.set(
                donation_uid, f"{msg.uuid}, {donation_time}, {amount_mob}, {code}"
            )
            await self.charities_balance_mmob.increment(code, amount_mmob)
            if msg.uuid in self.no_repay:
                self.no_repay.remove(msg.uuid)
            return (
                f"Your selected charity {await self.easter_eggs.get(code, code)} has been credited {amount_mob}MOB!\n"
                + "Thank you for your gift!"
            )
        if msg.uuid not in self.no_repay:
            if not await self.ask_yesno_question(
                utils.get_secret("ADMIN"),
                f"Approve refund request? {msg.source} sent payment of {amount_mob} when unexpected.",
            ):
                return None
            await self.send_typing(msg)
            payment_notif = await self.send_payment(msg.uuid, amount_pmob - FEE)
            await self.send_typing(msg, stop=True)
            if (
                not payment_notif
                or payment_notif
                and payment_notif.status == "tx_status_failed"
            ):
                return f"Failed to repay your {amount_mob} transaction; please contact the administrator with a screenshot for your MOB."
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.signal_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return "We have refunded your accidental payment, minus fees!"
        self.no_repay.remove(msg.uuid)
        return None


if __name__ == "__main__":
    run_bot(Hotline)
