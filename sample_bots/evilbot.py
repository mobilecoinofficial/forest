# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
from typing import Any
from aiohttp import web
from forest.core import Bot, Message, Response, app


class EvilBot(Bot):
    async def send_typing(self, recipient: str, stop: bool = False) -> None:
        typing_cmd: dict[str, Any] = {
            "command": "sendTyping",
            "recipient": [recipient],
        }
        if stop:
            typing_cmd["stop"] = stop

        await self.outbox.put(typing_cmd)

    async def handle_message(self, message: Message) -> Response:
        if message.typing == "STARTED":
            await self.send_typing(message.source)
            return None
        if message.typing == "STOPPED":
            await self.send_typing(message.source, stop=True)
            return None
        return await super().handle_message(message)

    async def default(self, _: Message) -> None:
        return None


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = EvilBot()

    web.run_app(app, port=8080, host="0.0.0.0")
