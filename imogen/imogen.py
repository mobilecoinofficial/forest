#!/usr/bin/python3.9
import asyncio
import json
import time
import logging
import os
from pathlib import Path
import aioredis
import aiohttp
from aiohttp import web

from forest import utils
from forest.main import Bot, Message, send_message_handler

url = (
    utils.get_secret("FLY_REDIS_CACHE_URL")
    or "redis://:ImVqcG9uMTdqMjc2MWRncjQi8a6c817565c7926c7c7e971b4782cf96a705bb20@forest-dev.redis.fly.io:10079"
)
password, rest = url.lstrip("redis://:").split("@")
host, port = rest.split(":")
redis = aioredis.Redis(host=host, port=int(port), password=password)


class Imogen(Bot):
    async def set_profile(self) -> None:
        profile = {
            "command": "updateProfile",
            "given-name": "imogen",
            "about": "imagine there's an imoge generated",
            "about-emoji": "\N{Artist Palette}",
            "family-name": "",
        }
        await self.signalcli_input_queue.put(profile)
        os.symlink(".", "state")
        logging.info(profile)

    async def do_imagine(self, msg: Message) -> str:
        logging.info(msg.full_text)
        logging.info(msg.text)
        await redis.rpush(
            "prompt_queue",
            json.dumps({"prompt": msg.text, "callback": msg.group or msg.source}),
        )
        # check if worker is up
        # if not, turn it on
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


async def store_image_handler(request: web.Request) -> web.Response:
    account = request.match_info.get("phonenumber")
    session = request.app.get("bot")
    if not session:
        return web.Response(status=504, text="Sorry, no live workers.")
    reader = await request.multipart()
    # /!\ Don't forget to validate your inputs /!\
    # reader.next() will `yield` the fields of your form
    field = await reader.next()
    if not isinstance(field, aiohttp.BodyPartReader):
        return web.Response(text="bad form")
    print(field.name)
    # assert field.name == "image"
    filename = field.filename or f"attachment-{time.time()}"
    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    path = Path(filename).absolute()
    with open(path, "wb") as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)
    message = request.query.get("message", "")
    args = f"-u +12406171657 send -a {path} -m {message} {account}".split()
    logging.info(args)
    await asyncio.create_subprocess_exec(
        "/home/sylv/forest/auxin/target/debug/auxin-cli", *args
    )
    return web.Response(
        text="{} sized of {} successfully stored" "".format(filename, size)
    )


app = web.Application()

app.add_routes(
    [
        web.post("/user/{phonenumber}", send_message_handler),
        web.post("/attachment/{phonenumber}", store_image_handler),
    ]
)


if __name__ == "__main__":
    Imogen().start_bot(app)
