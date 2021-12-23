#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
from aiohttp import web
from forest.core import Bot, Message, app


class TemplateBot(Bot):
    async def do_template(self, message) -> str:
        """
        A template you can fill in to make your own bot. Anything afteer do_ is a / command.
        Return value is used to send a message to the user.
         """
        return "template."

    async def do_hello(self, message) -> str:
        """
        Simple, Hello, world program. Type /hello and the bot will say "Hello, world."

        """
        return "Hello, world."



if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = TemplateBot()

    web.run_app(app, port=8080, host="0.0.0.0")
