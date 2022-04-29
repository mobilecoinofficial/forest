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

    async def do_type(self, message: Message) -> None:
        await self.send_typing(message)

    async def do_sticker(self, message: Message) -> None:
        await self.send_sticker(message)

    async def default(self, _: Message) -> None:
        return None

    async def do_lol(self, msg: Message) -> None:
        await self.send_reaction(msg, "\N{Face With Tears Of Joy}")


if __name__ == "__main__":
    run_bot(EvilBot)
