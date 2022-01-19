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
from forest.core import Message, PayBot, Response, app, hide, requires_admin
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")

from pdict import *


class QuestionBot(PayBot):
    def __init__(self):
        self.pending_confirmations: dict[str, asyncio.Future[bool]] = {}
        self.pending_answers: dict[str, asyncio.Future[Message]] = {}
        super().__init__()

    async def handle_message(self, message: Message) -> Response:
        if message.full_text and self.pending_answers.get(message.source):
            self.pending_answers[message.source].set_result(message)
            return "Thank you for your answer!"
        return await super().handle_message(message)

    async def do_yes(self, msg: Message) -> Response:
        """Handles 'yes' in response to a pending_confirmation."""
        if msg.source not in self.pending_confirmations:
            return "Did I ask you a question?"
        else:
            question = self.pending_confirmations.get(msg.source)
            question.set_result(True)

    async def do_no(self, msg: Message) -> Response:
        """Handles 'no' in response to a pending_confirmation."""
        if msg.source not in self.pending_confirmations:
            return "Did I ask you a question?"
        else:
            question = self.pending_confirmations.get(msg.source)
            question.set_result(False)

    async def do_askdemo(self, msg: Message) -> Response:
        """Asks a yes/no question."""
        if await self.ask_yesno_question(msg.source, "Are you feeling lucky, punk?"):
            return "well, that's good!"
        return "sending 🍀"

    async def do_askfreedemo(self, msg: Message) -> Response:
        answer = await self.ask_freeform_question(msg.source)
        if answer:
            return f"I love {answer} too!"

    async def ask_freeform_question(
        self, recipient: str, question_text: str = "What's your favourite colour?"
    ) -> str:
        await self.send_message(recipient, question_text)
        self.pending_answers[recipient] = asyncio.Future()
        answer = await self.pending_answers.get(recipient)
        self.pending_answers.pop(recipient)
        return answer.full_text

    async def ask_yesno_question(
        self, recipient: str, question_text: str = "Are you sure? yes/no"
    ) -> bool:
        self.pending_confirmations[recipient] = asyncio.Future()
        await self.send_message(recipient, question_text)
        result = await self.pending_confirmations[recipient]
        self.pending_confirmations.pop(recipient)
        return result


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

    @hide
    @requires_admin
    async def do_exception(self, _: Message) -> None:
        raise Exception("You asked for it!")

    @hide
    @requires_admin
    async def do_wait(self, _: Message) -> str:
        await asyncio.sleep(60)
        return "waited!"

    @hide
    @requires_admin
    async def do_eval(self, msg: Message) -> Response:
        """Evaluates a few lines of Python. Preface with "return" to reply with result."""

        async def async_exec(stmts: str, env: Optional[dict]) -> Any:
            parsed_stmts = ast.parse(stmts)
            fn_name = "_async_exec_f"
            my_fn = f"async def {fn_name}(): pass"
            parsed_fn = ast.parse(my_fn)
            for node in parsed_stmts.body:
                ast.increment_lineno(node)
            assert isinstance(parsed_fn.body[0], ast.AsyncFunctionDef)
            parsed_fn.body[0].body = parsed_stmts.body
            code = compile(parsed_fn, filename="<ast>", mode="exec")
            exec(code, env)  # pylint: disable=exec-used
            return await eval(f"{fn_name}()", env)  # pylint: disable=eval-used

        if msg.tokens and len(msg.tokens):
            source_blob = (
                msg.blob.get("content", {})
                .get("text_message", "")
                .replace("eval", "", 1)
                .replace("Eval", "", 1)
                .lstrip("/")
                .lstrip(" ")
            )
            if source_blob:
                return str(await async_exec(source_blob, locals()))
        return None

    @requires_admin
    async def do_balance(self, _: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"

    async def do_printerfact(self, _: Message) -> str:
        """Learn a fact about something."""
        if not utils.get_secret("FACT_SOURCE"):
            return "Sorry, no fact source configured!"
        async with self.client_session.get(utils.get_secret("FACT_SOURCE")) as resp:
            fact = await resp.text()
            return fact.strip()

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
        self.pending_orders: dict[str, str] = PersistDict("pending_orders")
        self.pending_orders: dict[str, str] = PersistDict("pending_orders")
        self.event_limits: dict[str, int] = PersistDict("event_limits")
        self.event_prompts: dict[str, str] = PersistDict("event_prompts")
        self.event_prices: dict[str, float] = PersistDict("event_prices")
        self.event_image_urls: dict[str, str] = PersistDict("event_images")
        self.event_owners: dict[str, list[str]] = PersistDict("event_owners")
        self.event_attendees: dict[str, list[str]] = PersistDict("event_attendees")
        self.event_lists: dict[str, list[str]] = PersistDict("event_lists")
        self.list_owners: dict[str, list[str]] = PersistDict("list_owners")
        self.easter_eggs: dict[str, str] = PersistDict("easter_eggs")
        self.successful_pays: dict[str, list[str]] = PersistDict("list_payouts")
        self.pay_lock: asyncio.Lock = asyncio.Lock()
        # okay, this now maps the tag (restore key) of each of the above to the instance of the PersistDict class
        self.state = {
            self.__getattribute__(attr).tag: self.__getattribute__(attr)
            for attr in dir(self)
            if isinstance(self.__getattribute__(attr), PersistDict)
        }
        super().__init__()

    @requires_admin
    async def do_dump(self, msg: Message) -> Response:
        return json.dumps(self.state, indent=2)

    @requires_admin
    async def do_dump2(self, msg: Message) -> Response:
        obj = (msg.arg1 or "").lower()
        dump = {}
        for eventcode in list(self.event_owners.keys()) + list(self.list_owners.keys()):
            event = {}
            for parameters in self.state:
                if self.state[parameters].get(eventcode):
                    event[parameters] = self.state[parameters].get(eventcode)
            dump[eventcode] = event
        return json.dumps(dump, indent=2)

    async def do_check(self, msg: Message) -> Response:
        obj = (msg.arg1 or "").lower()
        param = msg.arg2
        value = msg.arg3
        user = msg.source
        user_owns_event_obj = (
            obj in self.event_owners and user in self.event_owners.get(obj, [])
        )
        user_owns_list_obj = obj in self.list_owners and user in self.list_owners.get(
            obj, []
        )
        if user_owns_event_obj:
            return [
                f"code: {obj}",
                f"prompt: {self.event_prompts.get(obj)}",
                f"limit: {self.event_limits.get(obj)}",
                f"price: {self.event_prices.get(obj)}MOB/ea",
                f"owned by: {self.event_owners.get(obj)}",
                f"attendees: {self.event_attendees.get(obj)}",
                f"lists: {len(self.event_lists.get(obj,[]))} members",
            ]
        if user_owns_list_obj:
            return json.dumps(self.event_lists.get(obj, []), indent=2)
        return "You're not authorized."

    async def do_stop(self, msg: Message) -> Response:
        if (
            msg.arg1
            and msg.arg1 in self.event_lists
            and msg.source in self.event_lists.get(msg.arg1, [])
        ):
            self.event_lists[msg.arg1].remove(msg.source)
            return f"Okay, removed you from {msg.arg1}"
        elif not msg.arg1:
            for list_ in self.event_lists:
                if msg.source in self.event_lists[list_]:
                    self.event_lists[list_] = [
                        el for el in self.event_lists[list_] if msg.source != el
                    ]
                    await self.send_message(
                        msg.source,
                        f"Removed you from list {list_}, to rejoin send 'subscribe {list_}'",
                    )

    @requires_admin
    async def do_pay(self, msg: Message) -> Response:
        list_, amount, message = (
            (msg.arg1 or "").lower(),
            int((msg.arg2 or "0").lower()),
            msg.arg3 or msg.arg1,
        )
        to_send = []
        maybe_number = utils.signal_format(list_)
        if maybe_number:
            to_send = [maybe_number]
            await self.send_message(msg.source, f"okay, using {maybe_number}")
        if not len(to_send) and not (
            list_ in self.event_lists or list_ in self.event_attendees
        ):
            return "Sorry, that's not a valid list or number!"
        if not len(to_send):
            to_send = self.event_lists.get(list_, []) or self.event_attendees.get(
                list_, []
            )
        total = len(to_send) * amount
        await self.send_message(
            msg.source, f"about to send {total}mmob to {len(to_send)} folks on {list_}"
        )
        if not await self.ask_yesno_question(msg.source):
            return "OK, canceling"
        async with self.pay_lock:
            save_key = f"{list_}_{amount}_{message}"
            if save_key not in self.successful_pays:
                self.successful_pays[save_key] = []
            else:
                filtered_send_list = [
                    user
                    for user in to_send
                    if user not in self.successful_pays[save_key]
                ]
                if not len(filtered_send_list):
                    return "already sent to this combination, change the message to continue"
            utxos = await self.mobster.get_utxos()
            valid_utxos = [
                utxo
                for utxo, upmob in (await self.mobster.get_utxos()).items()
                if upmob > (1_000_000_000 * amount)
            ]
            if len(valid_utxos) < len(to_send):
                await self.send_message(
                    msg.source, "Insufficient number of utxos!\nBuilding more..."
                )
                building_msg = await self.mobster.split_txos_slow(
                    amount, (len(to_send) - len(valid_utxos))
                )
                await self.send_message(msg.source, building_msg)
                utxos = await self.mobster.get_utxos()
                valid_utxos = [
                    utxo
                    for utxo, upmob in (await self.mobster.get_utxos()).items()
                    if upmob > (1_000_000_000 * amount)
                ]
            failed = []
            # filter out users who have already received payments for a given combo of list+amount+message
            for target in [
                user for user in to_send if user not in self.successful_pays[save_key]
            ]:
                result = await self.send_payment(
                    recipient=target,
                    amount_pmob=amount * 1_000_000_000,
                    receipt_message=message,
                    input_txo_ids=[valid_utxos.pop(0)],
                )
                # if we didn't get a result indicating success
                if not result:
                    # stash as failed
                    failed += [target]
                else:
                    # persist user as successfully paid
                    self.successful_pays[save_key] += [target]
                await asyncio.sleep(1)
            await self.send_message(msg.source, f"failed on\n{failed}")
            return "completed sends"
        return "failed"

    async def do_send(self, msg: Message) -> Response:
        obj = msg.arg1
        param = msg.arg2
        if obj and param:
            try:
                result = await self.send_message(obj, param)
                return result
            except Exception as e:
                return str(e)

    async def do_blast(self, msg: Message) -> Response:
        """blast  <listname> "message"
        blast <eventname> "message"
        """
        obj, param, value = (msg.arg1 or "").lower(), (msg.arg2 or "").lower(), msg.arg3
        user = msg.source
        user_owns_list_obj = obj in self.list_owners and user in self.list_owners.get(
            obj, []
        )
        user_owns_event_obj = (
            obj in self.event_owners and user in self.event_owners.get(obj, [])
        )
        list_ = []
        sent = []
        success = False
        if (user_owns_list_obj or user_owns_event_obj) and param:
            success = True
            target_users = list(
                set(self.event_lists.get(obj, []) + self.event_attendees.get(obj, []))
            )
            if not await self.ask_yesno_question(
                "Are you sure you want to blast {len(target_users)}? (yes/no)"
            ):
                return "ok, let's not."
            for target_user in target_users:
                await self.send_message(target_user, param)
                sent.append(target_user)
                await asyncio.sleep(3)
        elif user_owns_event_obj or user_owns_list_obj:
            return "add a message"
        if not success:
            return "That didn't work!"
        return f"blasted {len(sent)} recipients on {obj}"

    async def do_subscribe(self, msg: Message) -> Response:
        obj = (msg.arg1 or "").lower()
        if obj in self.event_lists:
            if msg.source in self.event_lists[obj]:
                return f"You're already on the {obj} list!"
            else:
                self.event_lists[obj] += [msg.source]
                return f"Added you to the {obj} list!"
        else:
            return f"Sorry, I couldn't find a list called {obj} - to create your own, try 'add list {obj}'."

    async def do_help(self, msg: Message) -> Response:
        if msg.arg1 and msg.arg1.lower() == "add":
            return self.do_add.__doc__
        return "Welcome to The Hotline!\nEvents and announcement lists can be unlocked by messaging the bot the secret code at any time.\n\nAccolades, feature requests, and support questions can be directed to the project maintainers at https://signal.group/#CjQKILH5dkoz99TKxwG7T3TaVAuskMq4gybSplYDfTq-vxUrEhBhuy19A4DbvBqm7PfnBn3I ."

    async def do_remove(self, msg: Message) -> Response:
        """Removes a given event by code, if the msg source is the owner."""
        if not (
            msg.source in self.event_owners.get(msg.arg1, "")
            or msg.source in self.list_owners.get(msg.arg1, "")
        ):
            return f"Sorry, it doesn't look like you own {msg.arg1}."
        parameters = []
        for state_ in self.state.keys():
            if msg.arg1 in self.state[state_]:
                parameters += [state_]
        if await self.ask_yesno_question(
            msg.source, f"Are you sure you want to remove {msg.arg1} from {parameters}?"
        ):
            for state_ in self.state.keys():
                self.state[state_] = {
                    k: v for (k, v) in self.state[state_].items() if k != msg.arg1
                }
            return f"Okay, removed {msg.arg1}"

    async def do_set(self, msg: Message) -> Response:
        """Set is an alias for add"""
        return await self.do_add(msg)

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
        obj, param, value = (msg.arg1 or "").lower(), (msg.arg2 or "").lower(), msg.arg3
        user = msg.source
        user_owns_event_param = (
            param in self.event_owners and user in self.event_owners.get(param, [])
        )
        user_owns_list_param = (
            param in self.list_owners and user in self.list_owners.get(param, [])
        )
        objs = "event list owner price prompt limit invitees blast".split()
        success = False
        if (
            obj == "egg"
            and (
                param not in self.easter_eggs
                or msg.source in self.easter_eggs.get(param, "")
            )
            and value
        ):
            maybe_old_message = self.easter_eggs.get(param, "")
            if maybe_old_message:
                self.send_message(msg.source, f"replacing: {maybe_old_message}")
                self.easter_eggs[param] = f"{value} - updated by {msg.source}"
                return f"Updated {param} to read {value}"
            else:
                self.easter_eggs[param] = f"{value} - added by {msg.source}"
                return f'Added an egg! "{param}" now returns\n > {value} - added by {msg.source}'
        elif obj == "egg" and param in self.easter_eggs:
            return f"Sorry, egg already has value {self.easter_eggs.get(param)}. Please message support to change it."
        if (
            obj == "event"
            and param not in self.event_owners
            and param not in self.list_owners
        ):
            self.event_owners[param] = [user]
            self.event_prices[param] = None
            self.event_attendees[param] = []
            self.event_lists[param] = []
            self.event_image_urls[param] = ""
            self.list_owners[param] = [user]
            self.event_prompts[param] = ""
            successs = True
            return f'you now own event "{param}", and a list by the same name - time to add price, prompt, and invitees'
        if (
            obj == "list"
            and param not in self.list_owners
            and param not in self.event_owners
        ):
            self.event_lists[param] = []
            self.list_owners[param] = [user]
            self.event_image_urls[param] = ""
            return f"created list {param}, time to add some invitees and blast 'em"
        elif obj == "event" and not param:
            return ("please provide an event code to create!", "> add event TEAMNYE22")
        # if the user owns the event and we have a value passed
        if user_owns_event_param and value:
            if obj == "owner":
                self.event_owners[param] += [value]
                success = True
            elif obj == "price":
                # check if string == floatable
                if value.replace(".", "1", 1).isnumeric():
                    self.event_prices[param] = float(value)
                    success = True
                else:
                    return "provide a value that's a number please!"
            elif obj == "prompt":
                # todo add validation
                self.event_prompts[param] = value
                success = True
            elif obj == "limit":
                # check if int
                if value.isnumeric():
                    self.event_limits[param] = int(value)
                    success = True
                else:
                    return "please provide a value that's a number, please!"
        if user_owns_list_param and value:
            if obj == "invitees":
                self.event_lists[param] += [value]
                success = True
            elif obj == "prompt":
                # todo add validation
                self.event_prompts[param] = value
                success = True
            # "add blast coworkers "hey yall wanna grab a beer"
            # todo: confirm
            elif obj == "blast":
                for user in self.event_lists[param]:
                    await self.send_message(user, value)
                success = True
            elif obj == "limit":
                # check if int
                if value.isnumeric():
                    self.event_limits[param] = int(value)
                    success = True
                else:
                    return "please provide a value that's a number, please!"
        if success:
            return f"Successfully added {value} to event {param}'s {obj}!"

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
        code = msg.command
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
            code in self.event_owners  # event has an owner
            and code in self.event_prices  # and a price
            and len(self.event_attendees[code])
            < self.event_limits.get(code, 1e5)  # and there's space
            and msg.source
            not in self.event_attendees[code]  # and they're not already on the list
        ):
            self.pending_orders[msg.source] = code
            return [
                self.event_prompts.get(code) or "Event Unlocked!",
                f"You may now make one purchase of up to 2 tickets at {self.event_prices[code]} MOB ea.\nIf you have payments activated, open the conversation on your Signal mobile app, click on the plus (+) sign and choose payment.",
            ]
        # if there's a list but no attendees
        elif code in self.event_lists and code not in self.event_owners:
            if msg.source in self.event_lists[code]:
                return f"You're already on the {code} list!"
            elif not self.event_limits.get(code) or (
                self.event_limits.get(code)
                and len(self.event_lists.get(code)) < self.event_limits.get(code, 1000)
            ):
                self.event_lists[code] += [msg.source]
                if code in self.event_prompts and self.event_prompts.get(code):
                    await self.send_message(msg.source, self.event_prompts.get(code))
                return f"Added you to the {code} list!"
            else:
                return f"Sorry, {code} is full!"
        elif (
            code in self.event_owners  # event has owner
            and code
            in self.event_prices  # event has price (not just a stand-alone list)
            and msg.source in self.event_attendees[code]  # user on the list
        ):
            return f"You're already on the '{code}' list."
        elif code:
            lists = []
            all_owners = []
            for list_ in self.event_lists:
                # if user is on a list
                if msg.source in self.event_lists[list_]:
                    owners = self.event_owners.get(list_, [])
                    owners += self.list_owners.get(list_, [])
                    all_owners += owners
                    lists += [list_]
                # if user bought tickets
                if (
                    list_ in self.event_attendees
                    and msg.source in self.event_attendees[list_]
                ):
                    owners = self.event_owners.get(list_, [])
                    all_owners += owners
                    lists += [list_]
                # if user has started buying tickets
                maybe_pending = self.pending_orders.get(msg.source)
                if maybe_pending and maybe_pending in self.event_owners:
                    all_owners += self.event_owners.get(maybe_pending, [])
                    lists += [f"pending: {maybe_pending}"]

            try:
                maybe_user_profile = await self.auxin_req(
                    "getprofile", peer_name=msg.source
                )
                user_given = maybe_user_profile.blob.get("givenName", "givenName")
            except AttributeError:
                # this returns a Dict containing an error key
                user_given = "[error]"
            if code in self.easter_eggs:
                return self.easter_eggs.get(code)
            # being really lazy about owners / all_owners here
            for owner in list(set(all_owners)):
                # don't flood j
                if "7777" not in owner:
                    await self.send_message(
                        owner,
                        f"{user_given} ( {msg.source} ) says: {code} {msg.text}\nThey are in {list(set(lists))}",
                    )
                    await asyncio.sleep(0.1)
            return "Sorry, I can't help you with that!"

    @time_(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        amount_mob = float(mc_util.pmob2mob(amount_pmob).quantize(Decimal("1.0000")))
        if msg.source in self.pending_orders:
            code = self.pending_orders[msg.source].lower()
            price = self.event_prices.get(code, 1000)
            if amount_mob >= price and len(
                self.event_attendees.get(code, [])
            ) < self.event_limits.get(code, 1e5):
                if msg.source not in self.event_attendees[code]:
                    end_note = ""
                    if (amount_mob // price) == 2:
                        self.event_attendees[code] += [msg.source]
                        end_note = "(times two!)"
                    self.event_attendees[code] += [msg.source]
                    return f"Thanks for paying for {self.pending_orders[msg.source]}.\nYou're on the list! {end_note}"
        if msg.source not in self.no_repay:
            payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
            if not payment_notif:
                return None
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return "We have refunded your accidental payment, minus fees!"


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = ClanGat()
        out_app["kv_client"] = KVStoreClient()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
