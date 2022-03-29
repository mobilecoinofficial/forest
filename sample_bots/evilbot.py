# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
from forest.core import Bot, Message, Response, run_bot


class EvilBot(Bot):
    async def handle_message(self, message: Message) -> Response:
        if message.typing == "STARTED":
            await self.send_typing(message)
        if message.typing == "STOPPED":
            await self.send_typing(message, stop=True)
        return await super().handle_message(message)

    async def do_t(self, message: Message) -> None:
        await self.send_typing(message)

    async def default(self, _: Message) -> None:
        return None


if __name__ == "__main__":
    run_bot(EvilBot)
