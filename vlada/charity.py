#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import json
import time
from decimal import Decimal

from forest.core import (
    Message,
    Response,
    hide,
    requires_admin,
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
        self.last_prompted: aPersistDict[int] = aPersistDict("last_prompted")
        self.donations: aPersistDict[str] = aPersistDict("donations")
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
    async def do_dump(self, _: Message) -> Response:
        """dump
        returns a JSON serialization of current state"""
        return json.dumps({k: v.dict_ for (k, v) in self.state.items()}, indent=2)

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

    async def do_help(self, msg: Message) -> str:
        """Returns a link to the support channel."""
        help_dialog = await self.dialog.get("HELP", "HELP")
        return help_dialog

    async def default(self, message: Message) -> Response:
        # pylint: disable=too-many-return-statements,too-many-branches
        msg = message
        code = msg.arg0
        if not code:
            return None
        # if code == "?":
        #    return await self.do_help(msg)
        if msg.full_text and msg.full_text in [
            key.lower() for key in await self.easter_eggs.keys()
        ]:
            return await self.easter_eggs.get(msg.full_text)
        if code in await self.easter_eggs.keys():
            return await self.easter_eggs.get(code)
        await self.talkback(msg)
        # if it's been more than 60 seconds since we last prompted
        if (time.time() * 1000 - await self.last_prompted.get(msg.uuid, 0)) > 10 * 1000:
            await self.send_message(
                msg.uuid, await self.dialog.get("CHARITY_INFO", "CHARITY_INFO")
            )
            await self.send_message(
                msg.uuid, await self.dialog.get("HOW_TO_DONATE", "HOW_TO_DONATE")
            )
            await self.send_message(
                msg.uuid,
                None,
                attachments=["./how-to-donate.gif"],
            )
        await self.last_prompted.set(msg.uuid, int(time.time() * 1000))
        return None

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

        return await self.dialog.get("THANK_YOU_FOR_DONATION", "THANK_YOU_FOR_DONATION")


if __name__ == "__main__":
    run_bot(Charity)
