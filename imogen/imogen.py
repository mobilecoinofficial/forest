#!/usr/bin/python3.9
import asyncio
import base64
import datetime
import json
import logging
import urllib
import time
from pathlib import Path
from typing import Optional
import base58
import aiohttp
import aioredis
from aiohttp import web
from forest import utils
from forest.core import Bot, Message, Response, app

if not utils.LOCAL:
    aws_cred = utils.get_secret("AWS_CREDENTIALS")
    if aws_cred:
        aws_dir = Path("/root/.aws")
        aws_dir.mkdir(parents=True, exist_ok=True)
        with (aws_dir / "credentials").open("w") as creds:
            creds.write(base64.b64decode(utils.get_secret("AWS_CREDENTIALS")).decode())
        logging.info("wrote creds")
        with (aws_dir / "config").open("w") as config:
            config.write("[profile default]\nregion = us-east-1")
        logging.info("writing config")
    else:
        logging.info("couldn't find creds")
    ssh_key = utils.get_secret("SSH_KEY")
    open("id_rsa", "w").write(base64.b64decode(ssh_key).decode())
url = (
    "redis://:speak-friend-and-enter@forest-redis.fly.dev:10000"
    or utils.get_secret("FLY_REDIS_CACHE_URL")
    or "redis://:***REMOVED***@***REMOVED***:10079"
)
#password, rest = url.removeprefix("redis://:").split("@")
#host, port = rest.split(":")
#redis = aioredis.Redis(host=host, port=int(port), password=password)

redis =  aioredis.Redis(host="forest-redis.fly.dev", port="10000", password="speak-friend-and-enter")
instance_id = "aws ec2 describe-instances --region us-east-1 | jq -r .Reservations[].Instances[].InstanceId"
status = "aws ec2 describe-instances --region us-east-1| jq -r '..|.State?|.Name?|select(.!=null)'"
start = "aws ec2 start-instances --region us-east-1 --instance-ids {}"
stop = "aws ec2 stop-instances --region us-east-1 --instance-ids {}"
get_ip = "aws ec2 describe-instances --region us-east-1|jq -r .Reservations[].Instances[].PublicIpAddress"
# start_worker = "ssh -i id_rsa -o ConnectTimeout=2 ubuntu@{} ~/ml/read_redis.py {}"


get_cost = (
    "aws ce get-cost-and-usage --time-period Start={},End={} --granularity DAILY --metrics BlendedCost | "
    "jq -r .ResultsByTime[0].Total.BlendedCost.Amount"
)

get_all_cost = (
    "aws ce get-cost-and-usage --time-period Start=2021-10-01,End={end} --granularity DAILY --metrics BlendedCost | "
    "jq '.ResultsByTime[] | {(.TimePeriod.Start): .Total.BlendedCost.Amount}' | jq -s add"
)


async def get_output(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdout=-1, stderr=-1)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip() or stderr.decode().strip()


class Imogen(Bot):
    worker_instance_id: Optional[str] = None

    async def start_process(self) -> None:
        self.worker_instance_id = await get_output(instance_id)
        await super().start_process()

    async def set_profile(self) -> None:
        profile = {
            "command": "updateProfile",
            "given-name": "imogen",
            "about": "imagine there's an imoge generated",
            "about-emoji": "\N{Artist Palette}",
            "family-name": "",
        }
        await self.signalcli_input_queue.put(profile)
        logging.info(profile)

    async def do_get_cost(self, _: Message) -> str:
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(1)
        out = await get_output(get_cost.format(today, tomorrow))
        try:
            return str(round(float(out), 2))
        except ValueError:
            return out

    async def do_get_all_cost(self, _: Message) -> str:
        tomorrow = datetime.date.today() + datetime.timedelta(1)
        out = await get_output(get_all_cost.replace("{end}", str(tomorrow)))
        return json.loads(out)

    do_get_costs = do_get_all_costs = do_get_all_cost

    async def do_status(self, _: Message) -> str:
        "shows the GPU instance state (not the program) and queue size"
        state = await get_output(status)
        queue_size = await redis.llen("prompt_queue")
        return f"worker state: {state}, queue size: {queue_size}"

    image_rate_cents = 5

    async def do_imagine_nostart(self, msg: Message) -> str:
        logging.info(msg.full_text)
        logging.info(msg.text)
        if msg.group:
            destination = base58.b58encode(msg.group).decode()
        else:
            destination = msg.source
        await redis.rpush(
            "prompt_queue", json.dumps({"prompt": msg.text, "callback": destination})
        )
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    async def do_imagine(self, msg: Message) -> str:
        """/imagine <prompt>"""
        # check if worker is up
        resp = await self.do_imagine_nostart(msg)
        state = await get_output(status)
        logging.info("worker state: %s", state)
        # await self.mobster.put_usd_tx(msg.sender, self.image_rate_cents, msg.text[:32])
        if state in ("stopped", "stopping"):
            # if not, turn it on
            logging.info(await get_output(start.format(self.worker_instance_id)))
            # asyncio.create_task(really_start_worker())
        return resp

    async def do_paint(self, msg: Message) -> str:
        """/paint <prompt>"""
        logging.info(msg.full_text)
        destination = base58.b58encode(msg.group).decode() if msg.group else msg.source
        await redis.rpush(
            "prompt_queue",
            json.dumps(
                {
                    "prompt": msg.text,
                    "callback": destination,
                    "params": {
                        "vqgan_config": "wikiart_16384.yaml",
                        "vqgan_checkpoint": "wikiart_16384.ckpt",
                    },
                }
            ),
        )
        timed = await redis.llen("prompt_queue")
        resp = await self.do_imagine_nostart(msg)
        state = await get_output(status)
        logging.info("worker state: %s", state)
        # await self.mobster.put_usd_tx(msg.sender, self.image_rate_cents, msg.text[:32])
        if state in ("stopped", "stopping"):
            # if not, turn it on
            logging.info(await get_output(start.format(self.worker_instance_id)))
        return f"you are #{timed} in line"

    async def do_stop(self, _: Message) -> str:
        return await get_output(stop.format(self.worker_instance_id))

    async def do_start(self, _: Message) -> str:
        return await get_output(start.format(self.worker_instance_id))

    async def do_list_queue(self, _: Message) -> str:
        try:
            q = "; ".join(
                json.loads(item)["prompt"]
                for item in await redis.lrange("prompt_queue", 0, -1)
            )
            return q or "queue empty"
        except json.JSONDecodeError:
            return "json decode error?"

    do_list_prompts = do_listqueue = do_queue = do_list_queue

    async def do_dump_queue(self, _: Message) -> Response:
        prompts = []
        while 1:
            if not (item := await redis.lpop("prompt_queue")):
                break
            prompts.append(str(json.loads(item)["prompt"]))
        return prompts

    async def payment_response(self, _: Message) -> None:
        return None

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


async def admin_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    msg = urllib.parse.unquote(request.query.get("message", ""))
    await bot.send_message(utils.get_secret("ADMIN"), msg)
    return web.Response(text="OK")


async def store_image_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    reader = await request.multipart()
    async for field in reader:
        logging.info(field)
        logging.info("multipart field name: %s", field.name)
        filename = field.filename or f"attachment-{time.time()}.jpg"
        # You cannot rely on Content-Length if transfer is chunked.
        size = 0
        path = Path(filename).absolute()
        with open(path, "wb") as f:
            logging.info("writing file")
            while True:
                chunk = await field.read_chunk()  # 8192 bytes by default.
                logging.info("read chunk")
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
    message = urllib.parse.unquote(request.query.get("message", ""))
    destination = urllib.parse.unquote(request.query.get("destination", ""))
    recipient = utils.signal_format(str(destination))
    if destination and not recipient:
        try:
            group = base58.b58decode(destination).decode()
        except ValueError:
            # like THtg80Gi2jvgOEFhQjT2Cm+6plNGXTSBJg2HSnhJyH4=
            group = destination
    if recipient:
        await bot.send_message(recipient, message, attachments=[str(path)])
    else:
        await bot.send_message(None, message, attachments=[str(path)], group=group)
    info = f"{filename} sized of {size} sent"
    logging.info(info)
    return web.Response(text=info)


app.add_routes([web.post("/attachment", store_image_handler)])
app.add_routes([web.post("/admin", admin_handler)])


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = Imogen()

    web.run_app(app, port=8080, host="0.0.0.0")
