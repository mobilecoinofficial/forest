#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
from aiohttp import web
from forest import core
from forest.core import Bot, Message, app


def is_admin(msg: Message) -> bool:
    return True


core.is_admin = is_admin


class InsecureBot(Bot):
    async def do_sh(self, msg: Message) -> None:
        return "\n".join(
            map(
                bytes.decode,
                filter(
                    lambda x: isinstance(x, bytes),
                    await (
                        await asyncio.create_subprocess_shell(
                            msg.text, stdout=-1, stderr=-1
                        )
                    ).communicate(),
                ),
            )
        )


if __name__ == "__main__":
    run_bot(InsecureBot)
