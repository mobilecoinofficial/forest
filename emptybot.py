#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

from forest.core import Bot, Message, run_bot, QuestionBot
import logging

class HelloBot(QuestionBot):
    async def do_list(self, _: Message) -> str:
        print("BEFORE CALLING")
        result = await self.signal_rpc_request("listContacts", recipient="+17078902007")
        print("AFTER CALLING")
        return result


if __name__ == "__main__":
    run_bot(HelloBot)
