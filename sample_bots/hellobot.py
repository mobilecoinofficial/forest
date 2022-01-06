#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

from forest.core import Bot, Message, run_bot


class HelloBot(Bot):
    async def do_hello(self, _: Message) -> str:
        return "Hello, world!"


if __name__ == "__main__":
    run_bot(HelloBot)
