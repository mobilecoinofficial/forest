#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
import base64
import datetime
import json
import logging
import time
import random
import urllib
from pathlib import Path
from textwrap import dedent
from typing import Callable, Optional

import aioredis
import base58
import openai
from aiohttp import web

from forest import utils
from forest.core import (
    JSON,
    Message,
    Bot,
    Response,
    app,
    hide,
    requires_admin,
)

openai.api_key = utils.get_secret("OPENAI_API_KEY")

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
url = "redis://:speak-friend-and-enter@forest-redis.fly.dev:10000" or utils.get_secret(
    "FLY_REDIS_CACHE_URL"
)
# password, rest = url.removeprefix("redis://:").split("@")
# host, port = rest.split(":")
# redis = aioredis.Redis(host=host, port=int(port), password=password)

redis = aioredis.Redis(
    host="forest-redis.fly.dev", port=10000, password="speak-friend-and-enter"
)
instance_id = "aws ec2 describe-instances --region us-east-1 | jq -r .Reservations[].Instances[].InstanceId"
status = "gcloud --format json compute instances describe nvidia-gpu-cloud-image-1-vm | jq -r .status"
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
        await self.auxincli_input_queue.put(profile)
        logging.info(profile)

    # this is a really ugly non-cooperative inheritence
    async def handle_reaction(self, msg: Message) -> Response:
        """
        route a reaction to the original message.
        #if the number of reactions that message has is a fibonacci number, notify the message's author
        this is probably flakey, because signal only gives us timestamps and
        not message IDs
        """
        await super().handle_reaction(msg)
        assert msg.reaction
        if not msg.reaction.ts in self.sent_messages:
            logging.info("oh no")
            return None
        message_blob = self.sent_messages[msg.reaction.ts]
        current_reaction_count = len(message_blob["reactions"])
        reaction_counts = [
            len(some_message_blob["reactions"])
            for timestamp, some_message_blob in self.sent_messages.items()
            if len(some_message_blob["reactions"])
            # and timestamp > 1000*(time.time() - 3600)
        ]
        average_reaction_count = max(
            sum(reaction_counts) / len(reaction_counts) if reaction_counts else 0, 4
        )
        logging.info(
            "average reaction count: %s, current: %s",
            average_reaction_count,
            current_reaction_count,
        )
        if message_blob.get("paid"):
            logging.info("already notified about current reaction")
            return None
        if current_reaction_count < average_reaction_count:
            logging.info("average prompt count")
            return None
        prompt_author = message_blob.get("quote-author")
        if not prompt_author or prompt_author == self.bot_number:
            logging.info("message doesn't appear to be quoting anything")
            return None
        logging.debug("seding reaction notif")
        logging.info("setting paid=True")
        message_blob["paid"] = True
        message = f"\N{Object Replacement Character}, your prompt got {current_reaction_count} reactions. Congrats!"
        quote = {
            "quote-timestamp": msg.reaction.ts,
            "quote-author": self.bot_number,
            "quote-message": message_blob["message"],
            "mention": f"0:1:{prompt_author}",
        }
        if msg.group:
            await self.send_message(None, message, group=msg.group, **quote)
        else:
            await self.send_message(msg.source, message, **quote)
        await self.admin(f"need to pay {prompt_author}")
        # await self.send_payment_using_linked_device(prompt_author, await self.mobster.get_balance() * 0.1)
        return None

    @hide
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

    do_get_costs = do_get_all_costs = hide(do_get_all_cost)

    @hide
    async def do_status(self, _: Message) -> str:
        "shows the GPU instance state (not the program) and queue size"
        #state = await get_output(status)
        queue_size = await redis.llen("prompt_queue")
        # return f"worker state: {state}, queue size: {queue_size}"
        return f"queue size: {queue_size}"

    image_rate_cents = 10

    @hide
    async def do_imagine_nostart(self, msg: Message) -> str:
        if not msg.text and not msg.attachments:
            return "A prompt is required"
        logging.info(msg.full_text)
        if msg.group:
            destination = base58.b58encode(msg.group).decode()
        else:
            destination = msg.source
        params: JSON = {}
        if msg.attachments:
            attachment = msg.attachments[0]
            key = (
                "input/"
                + attachment["id"]
                + "-"
                + (attachment.get("filename") or ".jpg")
            )
            params["init_image"] = key
            await redis.set(
                key, open(Path("./attachments") / attachment["id"], "rb").read()
            )
        blob = {
            "prompt": msg.text,
            "callback": destination,
            "params": params,
            "timestamp": msg.timestamp,
            "author": msg.source,
            "url": utils.URL,
        }
        await redis.rpush(
            "prompt_queue",
            json.dumps(blob),
        )
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    async def do_imagine(self, msg: Message) -> str:
        """/imagine <prompt>"""
        # check if worker is up
        resp = await self.do_imagine_nostart(msg)
        # state = await get_output(status)
        # logging.info("worker state: %s", state)
        # # await self.mobster.put_usd_tx(msg.sender, self.image_rate_cents, msg.text[:32])
        # if state in ("stopped", "stopping"):
        #     # if not, turn it on
        #     output = await get_output(start.format(self.worker_instance_id))
        #     logging.info(output)
        #     if "InsufficientInstanceCapacity" in output:
        #         resp += ".\nsorry, andy jassy hates us. no gpu for us"
        #     # asyncio.create_task(really_start_worker())
        return resp

    def make_prefix(prefix: str) -> Callable:  # type: ignore  # pylint: disable=no-self-argument
        async def wrapped(self: "Imogen", msg: Message) -> str:
            msg.text = f"{prefix} {msg.text}"
            return await self.do_imagine(msg)

        wrapped.__doc__ = f"/{prefix} <prompt>: imagine it with {prefix} style"
        return wrapped

    do_mythical = make_prefix("mythical")
    do_festive = make_prefix("festive")
    do_dark_fantasy = make_prefix("dark fantasy")
    do_psychic = make_prefix("psychic")
    do_pastel = make_prefix("pastel")
    do_hd = make_prefix("hd")
    do_vibrant = make_prefix("vibrant")
    do_fantasy = make_prefix("fantasy")
    do_steampunk = make_prefix("steampunk")
    do_ukiyo = make_prefix("ukiyo")
    do_synthwave = make_prefix("synthwave")
    del make_prefix  # shouldn't be used after class definition is over

    async def do_quick(self, msg: Message) -> str:
        """Generate a 512x512 image off from the last time this command was used"""
        if not msg.text:
            return "A prompt is required"
        destination = base58.b58encode(msg.group).decode() if msg.group else msg.source
        blob = {
            "prompt": msg.text,
            "callback": destination,
            "feedforward": True,
            "timestamp": msg.timestamp,
            "author": msg.source,
            "url": utils.URL,
        }
        await redis.rpush(
            "prompt_queue",
            json.dumps(blob),
        )
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    async def do_fast(self, msg: Message) -> str:
        """Generate an image in a single pass"""
        if not msg.text:
            return "A prompt is required"
        destination = base58.b58encode(msg.group).decode() if msg.group else msg.source
        blob = {
            "prompt": msg.text,
            "callback": destination,
            "feedforward_fast": True,
            "timestamp": msg.timestamp,
            "author": msg.source,
            "url": utils.URL,
        }
        await redis.rpush(
            "prompt_queue",
            json.dumps(blob),
        )
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    async def do_paint(self, msg: Message) -> str:
        """/paint <prompt>"""
        if not msg.text and not msg.attachments:
            return "A prompt is required"
        logging.info(msg.full_text)
        destination = base58.b58encode(msg.group).decode() if msg.group else msg.source
        params: JSON = {
            "vqgan_config": "wikiart_16384.yaml",
            "vqgan_checkpoint": "wikiart_16384.ckpt",
        }
        if msg.attachments:
            attachment = msg.attachments[0]
            key = (
                "input/"
                + attachment["id"]
                + "-"
                + (attachment.get("filename") or ".jpg")
            )
            params["init_image"] = key
            await redis.set(
                key, open(Path("./attachments") / attachment["id"], "rb").read()
            )
        blob = {
            "prompt": msg.text,
            "callback": destination,
            "params": params,
            "timestamp": msg.timestamp,
            "author": msg.source,
            "url": utils.URL,
        }
        await redis.rpush(
            "prompt_queue",
            json.dumps(blob),
        )
        timed = await redis.llen("prompt_queue")
        # state = await get_output(status)
        # logging.info("worker state: %s", state)
        # # await self.mobster.put_usd_tx(msg.sender, self.image_rate_cents, msg.text[:32])
        # if state in ("stopped", "stopping"):
        #     # if not, turn it on
        #     logging.info(await get_output(start.format(self.worker_instance_id)))
        return f"you are #{timed} in line"

    @hide
    async def do_c(self, msg: Message) -> str:
        prompt = (
            "The following is a conversation with an AI assistant. "
            "The assistant is helpful, creative, clever, funny, very friendly, an artist and anarchist\n\n"
            "Human: Hello, who are you?\nAI: My name is Imogen, I'm an AI that makes dream-like images. What's up?\n"
            f"Human: {msg.text}\nAI: "
        )
        response = openai.Completion.create(  # type: ignore
            engine="davinci",
            prompt=prompt,
            temperature=0.9,
            max_tokens=140,
            top_p=1,
            frequency_penalty=0.0,
            presence_penalty=0.6,
            stop=["\n", " Human:", " AI:"],
        )
        return response["choices"][0]["text"].strip()

    @hide
    async def do_gpt(self, msg: Message) -> str:
        response = openai.Completion.create(  # type: ignore
            engine="davinci",
            prompt=msg.text,
            temperature=0.9,
            max_tokens=120,
            top_p=1,
            frequency_penalty=0.01,
            presence_penalty=0.6,
            stop=["\n", " Human:", " AI:"],
        )
        return response["choices"][0]["text"].strip()

    @hide
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

    do_list_prompts = do_listqueue = do_queue = hide(do_list_queue)

    @hide
    async def do_dump_queue(self, _: Message) -> Response:
        prompts = []
        while 1:
            if not (item := await redis.lpop("prompt_queue")):
                break
            prompts.append(str(json.loads(item)["prompt"]))
        return ", ".join(prompts)

    async def payment_response(self, msg: Message, amount_pmob: int) -> None:
        del msg, amount_pmob
        return None

    @hide
    async def do_poke(self, _: Message) -> str:
        return "poke"

    @requires_admin
    async def do_exception(self, msg: Message) -> None:
        raise Exception("You asked for it~!")

    async def default(self, message: Message) -> Response:
        if message.text and message.text.startswith("/"):
            message.text = message.text.removeprefix("/")
            return await self.do_imagine(message)
        return await super().default(message)

    async def do_tip(self, _: Message) -> str:
        return dedent(
            """
        Thank you for collaborating with Imogen, if you'd like to support the project you can send her a tip of any amount with Signal Pay.

        If you have payments activated, simply click on the plus sign and choose payment.

        If you don't have Payments activated follow these instructions to activate it.

        1. Update Signal app: https://signal.org/install/
        2. Open Signal, tap on the icon in the top left for Settings. If you don’t see *Payments*, reboot your phone. It can take a few hours.
        3. Tap *Payments* and *Activate Payments*

        For more information on Signal Payments visit:

        https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""
        )

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


tip_message = """
If you like Imogen's art, you can show your support by donating within Signal Payments.
Send Imogen a message with the command "/tip" for donation instructions.  Every time she creates an image, it costs $0.07
Imogen shares tips with collaborators! If you like an Imogen Imoge, react ❤️  t️o it. When an Imoge gets multiple reactions, the person who prompted the Imoge will be awarded a tip (currently 0.1 MOB).
""".strip()


async def store_image_handler(  # pylint: disable=too-many-locals
    request: web.Request,
) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    async for field in await request.multipart():
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
                # logging.info("read chunk")
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
    message = urllib.parse.unquote(request.query.get("message", ""))
    destination = urllib.parse.unquote(request.query.get("destination", ""))
    ts = int(urllib.parse.unquote(request.query.get("timestamp", "0")))
    author = urllib.parse.unquote(request.query.get("author", ""))
    recipient = utils.signal_format(str(destination))
    message += "\n\N{Object Replacement Character}"
    quote = (
        {
            "quote-timestamp": ts,
            "quote-author": author,
            "quote-message": "prompt",
            "mention": f"{len(message)-1}:1:{author}",
        }
        if author and ts
        else {}
    )
    if destination and not recipient:
        try:
            group = base58.b58decode(destination).decode()
        except ValueError:
            # like THtg80Gi2jvgOEFhQjT2Cm+6plNGXTSBJg2HSnhJyH4=
            group = destination
    if recipient:
        await bot.send_message(recipient, message, attachments=[str(path)], **quote)

    else:
        await bot.send_message(
            None, message, attachments=[str(path)], group=group, **quote
        )
        if random.random() < 0.05:
            await bot.send_message(None, tip_message, group=group)
    info = f"{filename} sized of {size} sent"
    logging.info(info)
    return web.Response(text=info)


app.add_routes([web.post("/attachment", store_image_handler)])
app.add_routes([])


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = Imogen()

    web.run_app(app, port=8080, host="0.0.0.0")
