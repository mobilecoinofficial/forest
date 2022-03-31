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
    run_bot,
)
from forest.pdictng import aPersistDict, aPersistDictOfInts, aPersistDictOfLists
from forest.extra import DialogBot
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)


class Charity(DialogBot):
    def __init__(self) -> None:
        self.easter_eggs: aPersistDict[str] = aPersistDict("easter_eggs")
        self.first_messages = aPersistDictOfInts("first_messages")
        self.donations: aPersistDict[str] = aPersistDict("donations")
        self.donation_rewards: aPersistDict[str] = aPersistDict("donation_rewards")
        self.reward_levels: aPersistDict[int] = aPersistDict("reward_levels")
        self.user_sessions: aPersistDictOfLists[str] = aPersistDictOfLists(
            "user_sessions"
        )
        self.charities_balance_mmob = aPersistDictOfInts("charities_balance_mmob")
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

    async def handle_message(self, message: Message) -> Response:
        """Method dispatch to do_x commands and goodies.
        Overwrite this to add your own non-command logic,
        but call super().handle_message(message) at the end"""
        # try to get a direct match, or a fuzzy match if appropriate
        if message.full_text and message.uuid not in await self.first_messages.keys():
            await self.first_messages.set(message.uuid, int(time.time() * 1000))
            if await self.dialog.get("FIRST_GREETING", ""):
                await self.send_message(
                    message.uuid, await self.dialog.get("FIRST_GREETING")
                )
        if message.full_text:
            await self.user_sessions.extend(message.uuid, message.full_text)
        return await super().handle_message(message)

    @hide
    async def do_fulfillment(self, msg: Message) -> Response:
        return await self.donation_rewards.get(await self.fulfillment(msg))

    async def fulfillment(self, msg: Message, donation_uid: str = get_uid()) -> str:
        user = msg.uuid
        await self.send_message(
            user,
            await self.dialog.get("THANK_YOU_WE_WILL_SHIP", "THANK_YOU_WE_WILL_SHIP"),
        )
        delivery_name = (await self.get_displayname(msg.uuid)).split("_")[0]
        if not await self.ask_yesno_question(
            user,
            f"Should we address your package to {delivery_name}?",
        ):
            delivery_name = await self.ask_freeform_question(
                user, "To what name should we address your package?"
            )
        delivery_address = await self.ask_address_question(
            user, require_confirmation=True
        )
        merchandise_size = await self.ask_multiple_choice_question(
            user,
            "What size shirt do you wear?",
            options={
                "XS": "Extra Small",
                "S": "Small",
                "M": "Medium",
                "L": "Large",
                "XL": "Extra Large",
            },
        )
        if await self.ask_yesno_question(
            user,
            "Would you like to provide an email address in case we need to contact you about your order? Otherwise, we will message you on Signal!",
        ):
            user_email = await self.ask_freeform_question(user, "What's your email?")
        else:
            user_email = None
        if not msg.source or await self.ask_yesno_question(
            user,
            "Would you like to provide an alternate phone number for your package? Otherwise, we will use the one you've registered on Signal!",
        ):
            user_phone = await self.ask_freeform_question(
                user, "What's your phone number?"
            )
        else:
            user_phone = msg.source
        await self.donation_rewards.set(
            donation_uid,
            f'{delivery_name}, "{delivery_address}", {merchandise_size}, {user_email}, {user_phone}',
        )
        await self.send_message(user, await self.dialog.get("GOT_IT", "GOT_IT"))
        return donation_uid

    async def default(self, message: Message) -> Response:
        # pylint: disable=too-many-return-statements,too-many-branches
        msg = message
        code = msg.arg0
        if not code:
            return None
        if code == "?":
            return await self.do_help(msg)
        if msg.full_text and msg.full_text in [
            key.lower() for key in await self.easter_eggs.keys()
        ]:
            return await self.easter_eggs.get(msg.full_text)
        if code in await self.easter_eggs.keys():
            return await self.easter_eggs.get(code)
        await self.talkback(msg)
        return await self.dialog.get("CHARITY_INFO", "CHARITY_INFO")

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        user = msg.uuid
        amount_mob = float(pmob2mob(amount_pmob).quantize(Decimal("1.0000")))
        amount_mmob = int(amount_mob * 1000)
        donation_uid = get_uid()
        donation_time = time.time()
        code = await self.dialog.get("CHARITY", "CHARITY")
        await self.donations.set(
            donation_uid, f"{user}, {donation_time}, {amount_mob}, {code}"
        )
        await self.charities_balance_mmob.increment(code, amount_mmob)
        if amount_mmob > await self.reward_levels.get(f"{code}_ship", 10_000):
            await self.fulfillment(msg, donation_uid)
            return None
        elif amount_mmob > await self.reward_levels.get(f"{code}_download", 10_000):
            return await self.dialog.get("THANK_YOU_PLEASE_DL", "THANK_YOU_PLEASE_DL")
        return await self.dialog.get("THANK_YOU_FOR_DONATION", "THANK_YOU_FOR_DONATION")


if __name__ == "__main__":

    run_bot(Charity)
