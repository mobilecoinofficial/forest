from aiohttp import web
from forest.core import Bot, Message, Response, app
from typing import Any


class EvilBot(Bot):
    async def send_typing(self, recipient: str, stop: bool = False) -> None:
        typing_cmd: dict[str, Any] = {
            "command": "sendTyping",
            "recipient": [recipient],
        }
        if stop:
            typing_cmd["stop"] = stop

        await self.auxincli_input_queue.put(typing_cmd)

    async def handle_message(self, msg: Message) -> Response:
        if msg.typing == "STARTED":
            await self.send_typing(msg.source)
            return None
        if msg.typing == "STOPPED":
            await self.send_typing(msg.source, stop=True)
            return None
        return await super().handle_message(msg)

    async def default(self, _: Message) -> None:
        return None


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = EvilBot()

    web.run_app(app, port=8080, host="0.0.0.0")
