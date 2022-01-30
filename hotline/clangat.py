#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import ast
import asyncio
import codecs
import hashlib
import json
import time
import logging
import os
from decimal import Decimal
from typing import Any, Optional

import aiohttp
import base58
from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time as time_
from prometheus_client import Summary

import mc_util
from forest import utils
from forest.core import (
    Message,
    QuestionBot,
    Response,
    app,
    hide,
    requires_admin,
    is_admin,
)
from forest.pdictng import aPersistDict
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


krng = open("/dev/urandom", "rb")


def r1dx(x: int = 20) -> int:
    """returns a random, fair integer from 1 to X as if rolling a dice with the specified number of sides"""
    max_r = 256
    assert x <= max_r
    while True:
        # get one byte, take as int on [1,256]
        r = int.from_bytes(krng.read(1), "little") + 1
        # if byte is less than the max factor of 'x' on the interval max_r, return r%x+1
        if r < (max_r - (max_r % x) + 1):
            return (r % x) + 1


class PayBotPro(QuestionBot):
    def __init__(self):
        self.last_seen: dict[str, str] = {}
        super().__init__()

    async def handle_message(self, message: Message) -> Response:
        user_last_seen = self.last_seen.get(message.source, 0)
        self.last_seen[message.source] = message.timestamp / 1000
        return await super().handle_message(message)

    async def do_signalme(self, _: Message) -> Response:
        """signalme
        Returns a link to share the bot with friends!"""
        return f"https://signal.me/#p/{self.bot_number}"

    @requires_admin
    async def do_balance(self, _: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"

    async def do_rot13(self, msg: Message) -> Response:
        """rot13 encodes the message.
        > rot13 hello world
        uryyb jbeyq"""
        return codecs.encode(msg.text, "rot13")

    async def do_roll(self, msg: Message) -> Response:
        """Rolls N dice of M sides: ie) roll 1 d20.
        Optionally accepts a third argument to specify starting at 0 instead of 1."""
        num_dice, dice_sides, offset = 1, 20, 0
        if msg.arg1 and msg.arg1.isnumeric():
            num_dice = int(msg.arg1)
        if msg.arg2 and msg.arg2.lstrip("d").isnumeric():
            dice_sides = int(msg.arg2.lstrip("d"))
        if msg.arg1 and "d" in msg.arg1:
            maybe_num_dice, maybe_dice_sides = msg.arg1.split("d")
            if maybe_num_dice.isnumeric():
                num_dice = int(maybe_num_dice)
            if maybe_dice_sides.isnumeric():
                dice_sides = int(maybe_dice_sides)
        if msg.arg3 and msg.arg3 == "0":
            offset = 1
        if dice_sides > 256:
            return "Try with a smaller number of sides (<256)."
        return [
            f"Okay, we rolled {num_dice} {dice_sides}-sided dice!"
            f"{[r1dx(dice_sides)-offset for _ in range(num_dice)]}"
        ]


class ClanGat(PayBotPro):
    def __init__(self):
        self.no_repay: list[str] = []
        self.address_cache: dict[str, str] = aPersistDict("address_cache")
        self.profile_cache: dict[str, Any] = aPersistDict("profile_cache")
        self.displayname_cache: dict[str, str] = aPersistDict("displayname_cache")
        self.displayname_lookup_cache: dict[str, str] = aPersistDict(
            "displayname_lookup_cache"
        )
        self.pending_orders: dict[str, str] = aPersistDict("pending_orders")
        self.pending_funds: dict[str, str] = aPersistDict("pending_funds")
        self.event_limits: dict[str, int] = aPersistDict("event_limits")
        self.event_prompts: dict[str, str] = aPersistDict("event_prompts")
        self.event_prices: dict[str, float] = aPersistDict("event_prices")
        self.event_images: dict[str, str] = aPersistDict("event_images")
        self.event_owners: dict[str, list[str]] = aPersistDict("event_owners")
        self.event_attendees: dict[str, list[str]] = aPersistDict("event_attendees")
        self.event_lists: dict[str, list[str]] = aPersistDict("event_lists")
        self.list_owners: dict[str, list[str]] = aPersistDict("list_owners")
        self.easter_eggs: dict[str, str] = aPersistDict("easter_eggs")
        self.successful_pays: dict[str, list[str]] = aPersistDict("successful_pays")
        self.payout_balance_mmob: dict[str, int] = aPersistDict("payout_balance_mmob")
        self.pay_lock: asyncio.Lock = asyncio.Lock()
        # okay, this now maps the tag (restore key) of each of the above to the instance of the PersistDict class
        self.state = {
            self.__getattribute__(attr).tag: self.__getattribute__(attr)
            for attr in dir(self)
            if isinstance(self.__getattribute__(attr), aPersistDict)
        }
        super().__init__()

    async def get_displayname(self, uuid):
        uuid = uuid.strip("\u2068\u2069")
        maybe_displayname = await self.displayname_cache.get(uuid)
        if maybe_displayname:
            return maybe_displayname
        maybe_user_profile = await self.profile_cache.get(uuid)
        if not maybe_user_profile:
            try:
                maybe_user_profile = (
                    await self.auxin_req(
                        "getprofile", peer_name=uuid.strip("\u2068\u2069")
                    )
                ).blob
                user_given = maybe_user_profile.get("givenName", "givenName")
                await self.profile_cache.set(uuid, maybe_user_profile)
            except AttributeError:
                # this returns a Dict containing an error key
                user_given = "[error]"
        else:
            user_given = maybe_user_profile.get("givenName", "")
        if "+" not in uuid and "-" in uuid:
            user_short = user_given + f"_{uuid.split('-')[1]}"
        else:
            user_short = user_given + uuid
        await self.displayname_cache.set(uuid, user_short)
        await self.displayname_lookup_cache.set(user_short, uuid)
        return user_short

    @requires_admin
    async def do_dump(self, msg: Message) -> Response:
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

    async def do_check(self, msg: Message) -> Response:
        obj = (msg.arg1 or "").lower()
        param = msg.arg2
        value = msg.arg3
        user = msg.uuid
        user_owns_event_obj = user in await self.event_owners.get(obj, [])
        user_owns_list_obj = user in await self.list_owners.get(obj, [])
        if user_owns_list_obj or user_owns_event_obj:
            return "\n\n".join(
                [
                    f"code: {obj}",
                    f"prompt: {await self.event_prompts.get(obj)}",
                    f"limit: {await self.event_limits.get(obj)}",
                    f"join price: {await self.event_prices.get(obj, 0)}MOB/ea",
                    f"event owned by: {[await self.get_displayname(uuid) for uuid in await self.event_owners.get(obj, [])]}",
                    f"announce list owned by: {[await self.get_displayname(uuid) for uuid in await self.list_owners.get(obj)]}",
                    f"number paid attendees: {len(await self.event_attendees.get(obj, []))}",
                    f"paid attendees: {[await self.get_displayname(uuid) for uuid in await self.event_attendees.get(obj, [])]}",
                    f"list has {len(await self.event_lists.get(obj,[]))} members",
                    f"list members: {[await self.get_displayname(uuid) for uuid in await self.event_lists.get(obj, [])]}",
                    f"balance: {await self.payout_balance_mmob.get(obj, 0)}mmob",
                ]
            )
        lists_ = [
            list_
            for list_ in await self.event_lists.keys()
            if msg.uuid in await self.event_lists.get(list_, [])
        ]
        owns_event = [
            list_
            for list_ in await self.event_lists.keys()
            if msg.uuid in await self.event_owners.get(list_, [])
        ]
        owns_list = [
            list_
            for list_ in await self.event_lists.keys()
            if msg.uuid in await self.list_owners.get(list_, [])
        ]
        return f"You're on the list for {lists_}.\n\nYou own these paid events: {owns_event}\n\nYou own these free lists: {owns_list}\n\nFor more information reply: check <code>."

    async def do_stop(self, msg: Message) -> Response:
        removed = 0
        if msg.arg1 and msg.uuid in await self.event_lists.get(
            (msg.arg1 or "").lower(), []
        ):
            await self.event_lists.remove_from((msg.arg1 or "").lower(), msg.uuid)
            return f"Okay, removed you from {msg.arg1}"
        elif not msg.arg1:
            for list_ in await self.event_lists.keys():
                if msg.uuid in await self.event_lists.get(list_, []):
                    await self.event_lists.remove_from(list_, msg.uuid)
                    await self.send_message(
                        msg.uuid,
                        f"Removed you from list {list_}, to rejoin send 'subscribe {list_}'",
                    )
                    removed += 1
        if msg.arg1 and not removed:
            "Sorry, you're not on the announcement list for {msg.arg1}"  # thanks y?!
        if not removed:
            return "You're not on any lists!"
        return None

    @hide
    async def do_payout(self, msg: Message) -> Response:
        user = msg.uuid
        list_ = (msg.arg1 or "").lower()
        user_owns_list_ = user in await self.event_owners.get(list_, [])
        balance = await self.payout_balance_mmob.get(list_, 0)
        if is_admin(msg) or (user_owns_list_ and balance):
            async with self.pay_lock:
                utxos = list((await self.mobster.get_utxos()).items())
                input_pmob_sum = 0
                input_txo_ids = []
                while input_pmob_sum < balance * 1_000_000_000:
                    txoid, pmob = utxos.pop()
                    input_txo_ids += [txoid]
                    input_pmob_sum += pmob
                    if len(input_txo_ids) > 15:
                        return "Something went wrong! Please contact your administrator for support. (too many utxos needed)"
                if not input_txo_ids:
                    return "Something went wrong! Please contact your administrator for support. (not enough utxos)"
                await self.send_message(msg.uuid, "Waiting for admin approval")
                if not await self.ask_yesno_question(
                    utils.get_secret("ADMIN"),
                    f"Owner of {list_} requests payout of {balance}. Approve?",
                ):
                    return "Sorry, admin rejected your payout."
                result = await self.send_payment(
                    recipient=user,
                    amount_pmob=(balance * 1_000_000_000 - FEE),
                    receipt_message=f'Payout for the "{list_}" event!',
                    input_txo_ids=input_txo_ids,
                )
                if result and not result.status == "tx_status_failed":
                    await self.payout_balance_mmob.decrement(list_, balance)
                    return f"Payed you you {balance}"
                return None
        if user_owns_list and not balance:
            return "Sorry, list {list_} has 0mmob balance!"  # thanks y?!
        return "Sorry, can't help you."

    @hide
    async def do_pay(self, msg: Message) -> Response:
        user = msg.uuid
        if not msg.arg2.isnumeric():
            return "Please provide an amount (in mmob) to pay!"
        list_, amount, message = (
            (msg.arg1 or "").lower(),
            int((msg.arg2 or "0").lower()),
            msg.arg3 or msg.arg1,
        )
        to_send = []
        maybe_number = utils.signal_format(list_)
        if maybe_number and not list_ in await self.event_lists.keys():
            to_send = [maybe_number]
            await self.send_message(msg.uuid, f"okay, using {maybe_number}")
        user_owns_list_ = user in await self.list_owners.get(list_, [])
        user_owns_event_ = user in await self.event_owners.get(list_, [])
        if not is_admin(msg) and not (user_owns_event_ or user_owns_list_):
            return "Sorry, you are not authorized."
        if not len(to_send) and not (
            list_ in await self.event_lists.keys()
            or list_ in await self.event_attendees.keys()
        ):
            return "Sorry, that's not a valid list or number!"
        if not len(to_send):
            to_send = await self.event_lists.get(
                list_, []
            ) or await self.event_attendees.get(list_, [])
        save_key = f"{list_}_{amount}_{message}"
        filtered_send_list = [
            user
            for user in to_send
            if user not in await self.successful_pays.get(save_key, [])
        ]
        total_mmob = len(filtered_send_list) * amount
        if len(to_send) and not len(filtered_send_list):
            return "already sent to this combination, change the message to continue"
        if not is_admin(msg) and (
            total_mmob > await self.payout_balance_mmob.get(list_, 0)
        ):
            return "Not enough balance remaining on this event!"
        await self.send_message(
            msg.uuid,
            f"about to send {total_mmob}mmob to {len(filtered_send_list)} folks on {list_}",
        )
        if not await self.ask_yesno_question(msg.uuid):
            return "OK, canceling"
        async with self.pay_lock:
            valid_utxos = [
                utxo
                for utxo, upmob in (await self.mobster.get_utxos()).items()
                if upmob > (1_000_000_000 * amount)
            ]
            if len(valid_utxos) < len(to_send):
                await self.send_message(
                    msg.uuid, "Insufficient number of utxos!\nBuilding more..."
                )
                building_msg = await self.mobster.split_txos_slow(
                    amount, (len(to_send) - len(valid_utxos))
                )
                await self.send_message(msg.uuid, building_msg)
                utxos = await self.mobster.get_utxos()
                valid_utxos = [
                    utxo
                    for utxo, upmob in (await self.mobster.get_utxos()).items()
                    if upmob > (1_000_000_000 * amount)
                ]
            failed = []
            for target in filtered_send_list:
                result = await self.send_payment(
                    recipient=target,
                    amount_pmob=amount * 1_000_000_000,
                    receipt_message=message,
                    input_txo_ids=[valid_utxos.pop(0)],
                )
                # if we didn't get a result indicating success
                if not result or (result and result.status == "tx_status_failed"):
                    # stash as failed
                    failed += [target]
                else:
                    # persist user as successfully paid
                    await self.payout_balance_mmob.decrement(list_, amount)
                    await self.successful_pays.extend(save_key, target)
                await asyncio.sleep(1)
            await self.send_message(
                msg.uuid,
                f"failed on\n{[await self.get_displayname(uuid) for uuid in failed]}",
            )
            return "completed sends"
        return "failed"

    @hide
    async def do_send(self, msg: Message) -> Response:
        obj = msg.arg1
        param = msg.arg2
        if obj and param:
            if obj in await self.displayname_lookup_cache.keys():
                obj = await self.displayname_lookup_cache.get(obj)
            try:
                result = await self.send_message(obj, param)
                return result
            except Exception as e:
                return str(e)

    @hide
    async def do_fund(self, msg: Message) -> Response:
        """Allows an owner to add funds for distribution to a list or event.
        fund <listname>
        fund <eventname>
        """
        obj, _, _ = (msg.arg1 or "").lower(), (msg.arg2 or ""), msg.arg3
        user = msg.uuid
        await self.pending_orders.remove(msg.uuid)
        user_owns_list_obj = (
            obj in await self.list_owners.keys()
            and user in await self.list_owners.get(obj, [])
        )
        user_owns_event_obj = (
            obj in await self.event_owners.keys()
            and user in await self.event_owners.get(obj, [])
        )
        if user_owns_event_obj or user_owns_list_obj:
            await self.pending_funds.set(user, obj)
            self.no_repay += [user]
            return "Okay, waiting for your funds."
        return "Sorry, can't find an event by that name."

    @hide
    async def do_blast(self, msg: Message) -> Response:
        """blast  <listname> "message"
        blast <eventname> "message"
        """
        obj, param, value = (msg.arg1 or "").lower(), (msg.arg2 or ""), msg.arg3
        user = msg.uuid
        user_owns_list_obj = (
            obj in await self.list_owners.keys()
            and user in await self.list_owners.get(obj, [])
        )
        user_owns_event_obj = (
            obj in await self.event_owners.keys()
            and user in await self.event_owners.get(obj, [])
        )
        list_ = []
        sent = []
        success = False
        if (user_owns_list_obj or user_owns_event_obj) and param:
            success = True
            target_users = list(
                set(
                    await self.event_lists.get(obj, [])
                    + await self.event_attendees.get(obj, [])
                )
            )
            # send preview
            await self.send_message(msg.uuid, param)
            # ask for confirmation
            if not await self.ask_yesno_question(
                msg.uuid,
                f"Are you sure you want to blast this to {len(target_users)}? (yes/no)",
            ):
                return "ok, let's not."
            # do the blast
            for target_user in target_users:
                await self.send_message(target_user.strip("\u2068\u2069"), param)
                sent.append(target_user)
                await asyncio.sleep(3)
        elif user_owns_event_obj or user_owns_list_obj:
            return "Try again - and add a message!"
        if not success:
            return "That didn't work! Try 'blast <list code> 'mymessage'. You can only send to lists you own!"
        # confirm we finished
        return f"Finished sending to {len(sent)} recipients on the {obj} list"

    @hide
    async def do_subscribe(self, msg: Message) -> Response:
        obj = (msg.arg1 or "").lower()
        if obj in await self.event_lists.keys():
            if msg.uuid in await self.event_lists[obj]:
                return f"You're already on the {obj} list!"
            else:
                await self.event_lists.extend(obj, msg.uuid)
                return f"Added you to the {obj} list!"
        else:
            return f"Sorry, I couldn't find a list called {obj} - to create your own, try 'add list {obj}'."

    async def do_help(self, msg: Message) -> Response:
        if msg.arg1 and msg.arg1.lower() == "add":
            return self.do_add.__doc__
        elif msg.arg1 and msg.arg1.lower() == "setup":
            return self.do_setup.__doc__
        return "\n\n".join(
            [
                "Hi, I'm MOBot! Welcome to my Hotline!",
                "\nEvents and announcement lists can be unlocked by messaging me the secret code at any time.\n\nAccolades, feature requests, and support questions can be directed to my maintainers at https://signal.group/#CjQKILH5dkoz99TKxwG7T3TaVAuskMq4gybSplYDfTq-vxUrEhBhuy19A4DbvBqm7PfnBn3I .",
            ]
        )

    @hide
    async def do_remove(self, msg: Message) -> Response:
        """Removes a given event by code, if the msg.uuid is the owner."""
        if not (
            msg.uuid in await self.event_owners.get(msg.arg1, "")
            or msg.uuid in await self.list_owners.get(msg.arg1, "")
        ):
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
        obj, param, value = (
            (msg.arg1 or "").lower(),
            (msg.arg2 or "").lower(),
            msg.arg3 or "",
        )
        value = value.strip("\u2068\u2069")
        user = msg.uuid
        user_owns_event_obj = obj and user in await self.event_owners.get(obj, [])
        user_owns_list_obj = obj and user in await self.list_owners.get(obj, [])
        event_or_list = "list" if user_owns_list_obj else None
        event_or_list = ("event" if user_owns_event_obj else None) or event_or_list
        if not event_or_list:
            return "Please try again with a list or event that you own!"
        if await self.ask_yesno_question(
            user,
            f"Would you like to limit the number of individuals who may join this {event_or_list}?",
        ):
            msg.arg1 = "limit"
            msg.arg2 = obj
            msg.arg3 = await self.ask_freeform_question(
                user, f"What limit would you like to set?"
            )
            await self.send_message(user, await self.do_add(msg))
        if user_owns_event_obj and await self.ask_yesno_question(
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
        user = msg.uuid
        user_owns_event_param = (
            param in await self.event_owners.keys()
            and user in await self.event_owners.get(param, [])
        )
        user_owns_list_param = (
            param in await self.list_owners.keys()
            and user in await self.list_owners.get(param, [])
        )
        objs = "event list owner price prompt limit".split()
        success = False
        if (obj == "egg" or obj == "easteregg") and (
            not await self.easter_eggs.get(param, None)
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
                self.send_message(msg.uuid, f"replacing: {maybe_old_message}")
                await self.easter_eggs.set(
                    param,
                    f"{value} - updated by {await self.get_displayname(msg.uuid)}",
                )
                return f"Updated {param} to read {value}"
            else:
                await self.easter_eggs.set(
                    param.lower(),
                    f"{value} - added by {await self.get_displayname(msg.uuid)}",
                )
                return f'Added an egg! "{param}" now returns\n > {value} - added by {await self.get_displayname(msg.uuid)}'
        elif obj == "egg" and param in await self.easter_eggs.keys():
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
            await self.event_images.set(param, [])
            await self.event_prompts.set(param, "")
            await self.payout_balance_mmob.set(param, 0)
            successs = True
            if await self.ask_yesno_question(
                user, f"Would you like to setup your new event '{param}' now? (yes/no)"
            ):
                msg.arg1 = param
                return await self.do_setup(msg)
            else:
                return f'You now own paid event "{param}", and a free list by the same name - use "setup {param}" to configure your event at your convenience!'
        if (
            obj == "list"
            and param
            and param not in await self.list_owners.keys()
            and param not in await self.event_owners.keys()
        ):
            await self.list_owners.set(param, [user])
            await self.event_lists.set(param, [])
            await self.event_images.set(param, [])
            await self.event_prompts.set(param, "")
            if await self.ask_yesno_question(
                user, f"Would you like to setup your new list '{param}' now? (yes/no)"
            ):
                msg.arg1 = param
                msg.arg2 = ""
                msg.arg3 = ""
                return await self.do_setup(msg)
            else:
                return f'You now own a free announcement list named {param} - use "setup {param}" to configure your event at your convenience!'
        elif obj == "event" and not param:
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid, "What unlock code would you like to use for this event?"
            )
            return await self.do_add(msg)
        elif obj == "list" and not param:
            msg.arg2 = await self.ask_freeform_question(
                msg.uuid, "What unlock code would you like to use for this list?"
            )
            return await self.do_add(msg)
        if (user_owns_event_param or user_owns_list_param) and not value:
            return await self.do_setup(msg)

        # if the user owns the event and we have a value passed
        if user_owns_event_param and value:
            if obj == "owner":
                new_owner_uuid = await self.displayname_cache.get(value, value)
                await self.send_message(
                    new_owner_uuid,
                    f"You've been added as an owner of {value} by {await self.displayname_cache.get(msg.uuid)}",
                )
                await self.event_owners.extend(param, value)
                success = True
            elif obj == "price":
                # check if string == floatable
                if (
                    value.replace(".", "1", 1).isnumeric()  # 1.01
                    or value.replace(",", "1", 1).isnumeric()  # 1,01
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
        if user_owns_list_param and value:
            if obj == "owner":
                new_owner_uuid = await self.displayname_cache.get(value, value)
                await self.send_message(
                    new_owner_uuid,
                    f"You've been added as an owner of {value} by {await self.displayname_cache.get(msg.uuid)}",
                )
                await self.list_owners.extend(param, value)
                success = True
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

    @hide
    async def do_purchase(self, _: Message) -> Response:
        helptext = """If you have payments activated, open the conversation on your Signal mobile app, click on the plus (+) sign and choose payment.\n\nIf you don't have Payments activated follow these instructions to activate it.

1. Update Signal app: https://signal.org/install/
2. Open Signal, tap on the icon in the top left for Settings. If you don’t see *Payments*, reboot your phone. It can take a few hours.
3. Tap *Payments* and *Activate Payments*

For more information on Signal Payments visit:

https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""
        return helptext

    @hide
    async def do_buy(self, message: Message) -> Response:
        return await self.do_purchase(message)

    async def default(self, msg: Message) -> Response:
        code = msg.arg0
        if code == "+":
            return await self.do_purchase(msg)
        elif code == "?":
            return await self.do_help(msg)
        elif code == "y":
            return await self.do_yes(msg)
        elif code == "n":
            return await self.do_no(msg)
        # if the event has an owner and a price and there's attendee space and the user hasn't already bought tickets
        if (
            code
            and code in await self.event_owners.keys()  # event has an owner
            and code in await self.event_prices.keys()  # and a price
            and len(await self.event_attendees.get(code, []))
            < await self.event_limits.get(code, 1e5)  # and there's space
            and msg.uuid
            not in await self.event_attendees[
                code
            ]  # and they're not already on the list
        ):
            self.pending_orders[msg.uuid] = code
            return [
                await self.event_prompts.get(code) or "Event Unlocked!",
                f"You may now make one purchase of up to 2 tickets at {await self.event_prices[code]} MOB ea.\nIf you have payments activated, open the conversation on your Signal mobile app, click on the plus (+) sign and choose payment.",
            ]
        # if there's a list but no attendees
        elif (
            code  # if there's a code and...
            and code in await self.event_lists.keys()  # if there's a list and...
            and not await self.event_prices.get(code, 0)  # and it's free
        ):
            if msg.uuid in await self.event_lists[code]:
                return f"You're already on the {code} list!"
            elif not await self.event_limits.get(code) or (
                len(await self.event_lists.get(code, []))
                < await self.event_limits.get(code, 1000)
            ):
                if await self.ask_yesno_question(
                    msg.uuid,
                    f"You've unlocked the {code} list! Would you like to subscribe to this announcement list?",
                ):
                    if await self.event_prompts.get(code):
                        await self.send_message(
                            msg.uuid, await self.event_prompts.get(code)
                        )
                    await self.event_lists.extend(code, msg.uuid)
                    return f"Added you to the {code} list!"
                else:
                    return f"Okay, but you're missing out! \n\nIf you change your mind, unlock the list again by sending '{code}'"
            else:
                return f"Sorry, {code} is full!"
        elif (
            code
            and code in await self.event_owners.keys()
            and code  # event has owner
            in await self.event_prices.keys()  # event has price (not just a stand-alone list)
            and code in await self.event_lists.keys()  # if there's a list and...
            and msg.uuid in await self.event_attendees[code]  # user on the list
        ):
            return f"You're already on the '{code}' list."
        # handle default case
        elif code:
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
                    and msg.uuid in await self.event_attendees[list_]
                ):
                    owners = await self.event_owners.get(list_, [])
                    all_owners += owners
                    lists += [list_]
                # if user has started buying tickets
                maybe_pending = await self.pending_orders.get(msg.uuid)
                if maybe_pending and maybe_pending in await self.event_owners.keys():
                    all_owners += await self.event_owners.get(maybe_pending, [])
                    lists += [f"pending: {maybe_pending}"]

            user_given = await self.get_displayname(msg.uuid)
            if msg.full_text in [key.lower() for key in await self.easter_eggs.keys()]:
                return await self.easter_eggs.get(msg.full_text)
            if code in await self.easter_eggs.keys():
                return await self.easter_eggs.get(code)
            # being really lazy about owners / all_owners here
            for owner in list(set(all_owners)):
                # don't flood j
                if "7777" not in owner:
                    await self.send_message(
                        owner,
                        f"{user_given} ( {msg.source} ) says: {code} {msg.text}\nThey are on the following lists: {list(set(lists))}",
                    )
                    await asyncio.sleep(0.1)
            return "Sorry, I can't help you with that! I'll see if I can find someone who can..."

    @time_(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        amount_mob = float(mc_util.pmob2mob(amount_pmob).quantize(Decimal("1.0000")))
        amount_mmob = int(amount_mob * 1000)
        if msg.uuid in await self.pending_orders.keys():
            code = (await self.pending_orders[msg.uuid]).lower()
            price = await self.event_prices.get(code, 1000)
            if amount_mob >= price and len(
                await self.event_attendees.get(code, [])
            ) < await self.event_limits.get(code, 1e5):
                if msg.uuid not in await self.event_attendees.get(code, []):
                    await self.payout_balance_mmob.increment(code, amount_mmob)
                    end_note = ""
                    if (amount_mob // price) == 2:
                        await self.event_attendees.extend(code, msg.uuid)
                        end_note = "(times two!)"
                    await self.event_attendees.extend(code, msg.uuid)
                    thank_you = f"Thanks for paying for {await self.pending_orders[msg.uuid]}.\nYou're on the list! {end_note}"
                    await self.pending_orders.remove(msg.uuid)
                    return thank_you
        if msg.uuid in await self.pending_funds.keys():
            code = await self.pending_funds.pop(msg.uuid)
            await self.payout_balance_mmob.increment(code, amount_mmob)
            self.no_repay.remove(msg.uuid)
            return (
                f"We have credited your event {code} {amount_mob}MOB!\n"
                + "You may sweep your balance with 'payout' or distrbute specific amounts of millimobb to attendees and individuals with 'pay <user_or_group> <amount> <memo>'."
            )
        if msg.uuid not in self.no_repay:
            if not await self.ask_yesno_question(
                utils.get_secret("ADMIN"),
                f"Approve refund request? {msg.source} sent payment of {amount_mob} when unexpected.",
            ):
                return None
            payment_notif = await self.send_payment(msg.uuid, amount_pmob - FEE)
            if payment_notif.status == "tx_status_failed":
                return f"Failed to repay your {amount_mob} transaction; please contact the administrator with a screenshot for your MOB."
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return "We have refunded your accidental payment, minus fees!"
        self.no_repay.remove(msg.uuid)
        return None


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = ClanGat()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
