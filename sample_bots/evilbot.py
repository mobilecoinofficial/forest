# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
from typing import Any
from aiohttp import web
from forest.core import Bot, Message, Response, run_bot, rpc


class EvilBot(Bot):
    async def send_typing(self, recipient: str, stop: bool = False) -> None:
        await self.outbox.put(typing_cmd)

    async def handle_message(self, message: Message) -> Response:
        if message.typing == "STARTED":
            await self.outbox.put(rpc("sendTyping", recipient=[recipient]))
        if message.typing == "STOPPED":
            await self.outbox.put(rpc("sendTyping", recipient=[recipient], stop=True))
        return await super().handle_message(message)

    async def default(self, _: Message) -> None:
        return None


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = EvilBot()

    web.run_app(app, port=8080, host="0.0.0.0")
