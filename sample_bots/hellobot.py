#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
from aiohttp import web
from forest.core import Bot, Message, app


class HelloBot(Bot):
    async def do_hello(self, message) -> None:
        async def concurrently() -> None:
            return "Hello, world."

        asyncio.create_task(concurrently())


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = HelloBot()

    web.run_app(app, port=8080, host="0.0.0.0")
