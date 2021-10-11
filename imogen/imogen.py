#!/usr/bin/python3.9
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
import aioredis
from aiohttp import web
from phonenumbers import NumberParseException

sys.path.append("..")
sys.path.append("../forest")  # facepalm
from forest import datastore, utils
from forest.main import Bot, Message

url = (
    utils.get_secret("FLY_REDIS_CACHE_URL")
    or "redis://:***REMOVED***@***REMOVED***:10079"
)
password, rest = url.lstrip("redis://:").split("@")
host, port = rest.split(":")
redis = aioredis.Redis(host=host, port=port, password=password)


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
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


async def send_message_handler(request: web.Request) -> web.Response:
    account = request.match_info.get("phonenumber")
    session = request.app.get("bot")
    if not session:
        return web.Response(status=504, text="Sorry, no live workers.")
    msg_data = await request.text()
    await session.send_message(
        account, msg_data, endsession=request.query.get("endsession")
    )
    return web.json_response({"status": "sent"})


async def store_image_handler(request: web.Request) -> web.Response:
    account = request.match_info.get("phonenumber")
    session = request.app.get("bot")
    if not session:
        return web.Response(status=504, text="Sorry, no live workers.")
    reader = await request.multipart()
    # /!\ Don't forget to validate your inputs /!\
    # reader.next() will `yield` the fields of your form
    field = await reader.next()
    print(field.name)
    assert field.name == "image"
    filename = field.filename
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
    message = request.query.get("message", ""),
    args = f"-u ***REMOVED*** send -a {path} -m {message} {account}".split()
    logging.info(args)
    proc = await asyncio.create_subprocess_exec(
        "/home/sylv/forest/auxin/target/debug/auxin-cli", *args
    )
    return web.Response(
        text="{} sized of {} successfully stored" "".format(filename, size)
    )


app = web.Application()


async def start_bot(our_app: web.Application) -> None:
    try:
        number = utils.signal_format(sys.argv[1])
    except IndexError:
        number = utils.get_secret("BOT_NUMBER")
    our_app["bot"] = bot = Imogen(number)
    asyncio.create_task(bot.start_process())
    asyncio.create_task(bot.handle_messages())


app.on_startup.append(start_bot)
if not utils.get_secret("NO_MEMFS"):
    app.on_startup.append(datastore.start_memfs)
    app.on_startup.append(datastore.start_memfs_monitor)
app.add_routes(
    [
        web.post("/user/{phonenumber}", send_message_handler),
        web.post("/attachment/{phonenumber}", store_image_handler),
    ]
)

app["bot"] = None


if __name__ == "__main__":
    logging.info("new run".center(60, "="))
    web.run_app(app, port=8080, host="0.0.0.0")
