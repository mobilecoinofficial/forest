#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

from forest.core import Bot, Message, run_bot


class TemplateBot(Bot):
    async def do_template(self, _: Message) -> str:
        """
        A template you can fill in to make your own bot. Anything after do_ is a / command.
        Return value is used to send a message to the user.
        """
        return "template."

    async def do_hello(self, _: Message) -> str:
        """
        Simple, Hello, world program. Type /hello and the bot will say "Hello, world!"

        """
        return "Hello, world!"

    async def do_echo(self, message: Message) -> str:
        """
        Repeats what you said. Type /echo foo and the bot will say "foo".
        """
        return message.text


if __name__ == "__main__":
    run_bot(TemplateBot)
