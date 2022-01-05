#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
import base64
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import Callable, Optional

import aioredis
import openai
from aiohttp import web

from forest import pghelp, utils
from forest.core import JSON, Bot, Message, Response, app, hide, requires_admin, run_bot

# @dataclass
# class InsertedPrompt:
#     prompt: str
#     paid: bool
#     author: str
#     signal_ts: int
#     group: str = ""
#     params: str = "{}"
#     url: str = utils.URL

#     async def insert(self, queue: pghelp.PGInterface) -> None:
#         async with queue.pool.acquire() as conn:
#             await conn.execute(
#                 """INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7)
#                 RETURNING (SELECT count(id) AS len FROM prompt_queue WHERE status <> 'done');""",
#                 self.prompt,
#                 msg.text,
#                 False,
#                 msg.source,
#                 msg.timestamp,
#                 msg.group,
#                 json.dumps(params),
#                 utils.URL,
#             )


QueueExpressions = pghelp.PGExpressions(
    table="prompt_queue",
    create_table="""CREATE TABLE {self.table} (
        id SERIAL PRIMARY KEY,
        prompt TEXT,
        paid BOOLEAN,
        author TEXT,
        signal_ts BIGINT,
        group_id TEXT DEFAULT null,
        status TEXT DEFAULT 'pending',
        assigned_at TIMESTAMP DEFAULT null,
        params TEXT DEFAULT null,
        response_ts BIGINT DEFAULT null,
        reaction_count INTEGER DEFAULT 0,
        reaction_map JSONB DEFAULT jsonb '{}',
        elapsed_gpu INT DEFAULT null,
        loss FLOAT DEFAULT null,
        filepath TEXT DEFAULT null,
        version TEXT DEFAULT null,
        hostname TEXT DEFAULT null,
        url TEXT DEFAULT 'https://imogen-renaissance.fly.dev/',
        sent_ts BIGINT DEFAULT null,
        errors INTEGER DEFAULT 0);""",
    insert="""INSERT INTO {self.table} (prompt, paid, author, signal_ts, group_id, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7);""",
    length="SELECT count(id) AS len FROM {self.table} WHERE status='pending' OR status='assigned';",
    list_queue="SELECT prompt FROM {self.table} WHERE status='pending' OR status='assigned' ORDER BY signal_ts ASC",
    react="UPDATE {self.table} SET reaction_map = reaction_map || $2::jsonb WHERE sent_ts=$1;",
)

openai.api_key = utils.get_secret("OPENAI_API_KEY")
# gcloud beta compute instances create imogen-3 --project=sublime-coast-306000 --zone=us-central1-a
# --machine-type=n1-standard-4 --network-interface=network-tier=PREMIUM,subnet=default
# --metadata=google-monitoring-enable=0,google-logging-enable=0 --maintenance-policy=TERMINATE
# --service-account=638601660045-compute@developer.gserviceaccount.com
# --scopes=https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/devstorage.read_only
# --accelerator=count=1,type=nvidia-tesla-v100 --min-cpu-platform=Automatic
# --tags=http-server,https-server --no-shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
# --labels=goog-dm=nvidia-gpu-cloud-image-1 --reservation-affinity=any --source-machine-image=bulky


async def get_output(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdout=-1, stderr=-1)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip() or stderr.decode().strip()


if not utils.LOCAL:
    gcp_cred = utils.get_secret("GCP_CREDENTIALS")
    if gcp_cred:
        open("gcp-key-imogen.json", "w").write(base64.b64decode(gcp_cred).decode())
        # get_output("gcloud auth activate-service-account --key-file gcp-key-imogen.json")
        # get_output("gcloud config set project sublime-coast-306000")
    else:
        logging.info("couldn't find gcp creds")
    ssh_key = utils.get_secret("SSH_KEY")
    open("id_rsa", "w").write(base64.b64decode(ssh_key).decode())

# url = "redis://:speak-friend-and-enter@forest-redis.fly.dev:10000" or utils.get_secret("FLY_REDIS_CACHE_URL")
# password, rest = url.removeprefix("redis://:").split("@")
# host, port = rest.split(":")
# redis = aioredis.Redis(host=host, port=int(port), password=password)

redis = aioredis.Redis(
    host="forest-redis.fly.dev", port=10000, password="speak-friend-and-enter"
)

status = "gcloud --format json compute instances describe nvidia-gpu-cloud-image-1-vm | jq -r .status"
start = "gcloud --format json compute instances start nvidia-gpu-cloud-image-1-vm | jq -r .status"
systemctl = "yes | gcloud --format json compute ssh start nvidia-gpu-cloud-image-1-vm -- systemctl status imagegen"


class Imogen(Bot):
    worker_instance_id: Optional[str] = None

    async def start_process(self) -> None:
        self.queue = pghelp.PGInterface(
            query_strings=QueueExpressions,
            database=utils.get_secret("DATABASE_URL"),
        )
        # get_output("gcloud auth activate-service-account -f gcp-key-imogen.json")
        await self.admin("starting")
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

    async def handle_reaction(self, msg: Message) -> Response:
        await super().handle_reaction(msg)
        assert msg.reaction
        logging.info(
            "updating %s with %s",
            msg.reaction.ts,
            json.dumps({msg.source: msg.reaction.emoji}),
        )
        await self.queue.react(
            msg.reaction.ts, json.dumps({msg.source: msg.reaction.emoji})
        )
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
            sum(reaction_counts) / len(reaction_counts) if reaction_counts else 0, 6
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

    async def do_status(self, _: Message) -> str:
        "shows queue size"
        queue_length = (await self.queue.length())[0].get("len")
        return f"queue size: {queue_length}"

    image_rate_cents = 10

    async def insert(
        self, msg: Message, parms: dict, attachments: bool = False
    ) -> None:
        pass
        # if msg.attachments and attachments
        # params is the only thing that changes between commands/models
        # also handles rate limiting, paid success checking
        # will start instances if paid
        # future: instance-groups resize {} --size {}

    async def ensure_worker(self) -> None:
        if get_output(status) == "TERMINATED":
            await self.admin(await get_output(start))

    async def do_imagine(self, msg: Message) -> str:
        """/imagine [prompt]"""
        if not msg.text.strip() and not msg.attachments:
            return "A prompt is required"
        # await self.mobster.put_usd_tx(msg.sender, self.image_rate_cents, msg.text[:32])
        logging.info(msg.full_text)
        params: JSON = {}
        if msg.attachments:
            attachment = msg.attachments[0]
            if attachment.get("filename", "").endswith(".txt"):
                return "your prompt is way too long"
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
        await self.queue.execute(
            """INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7);""",
            msg.text,
            False,
            msg.source,
            msg.timestamp,
            msg.group,
            json.dumps(params),
            utils.URL,
        )
        await self.ensure_worker()
        queue_length = (await self.queue.length())[0].get("len")
        return f"you are #{queue_length} in line"

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
        await self.queue.execute(
            """INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7);""",
            msg.text,
            False,
            msg.source,
            msg.timestamp,
            msg.group,
            json.dumps({"feedforward": True}),
            utils.URL,
        )
        await self.ensure_worker()
        queue_length = (await self.queue.length())[0].get("len")
        return f"you are #{queue_length} in line"

    async def do_fast(self, msg: Message) -> str:
        """Generate an image in a single pass"""
        if not msg.text:
            return "A prompt is required"
        await self.ensure_worker()
        await self.queue.execute(
            """INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7);""",
            msg.text,
            False,
            msg.source,
            msg.timestamp,
            msg.group,
            json.dumps({"feedforward_fast": True}),
            utils.URL,
        )
        queue_length = (await self.queue.length())[0].get("len")
        return f"you are #{queue_length} in line"

    async def do_paint(self, msg: Message) -> str:
        """/paint <prompt>"""
        if not msg.text and not msg.attachments:
            return "A prompt is required"
        logging.info(msg.full_text)
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
        await self.queue.execute(
            """INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7);""",
            msg.text,
            False,
            msg.source,
            msg.timestamp,
            msg.group,
            json.dumps(params),
            utils.URL,
        )
        await self.ensure_worker()
        queue_length = (await self.queue.length())[0].get("len")
        return f"you are #{queue_length} in line"

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

    @requires_admin
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

    async def do_list_queue(self, _: Message) -> str:
        q = "; ".join(prompt.get("prompt") for prompt in await self.queue.list_queue())
        return q or "queue empty"

    do_list_prompts = do_listqueue = do_queue = hide(do_list_queue)

    @hide
    async def do_dump_queue(self, _: Message) -> Response:
        raise NotImplementedError

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

        If you get "This person has not activated payments", try messagining me with /ping. 

        If you have payments activated, simply click on the plus sign and choose payment.

        If you don't have Payments activated follow these instructions to activate it.

        1. Update Signal app: https://signal.org/install/
        2. Open Signal, tap on the icon in the top left for Settings. If you don’t see *Payments*, reboot your phone. It can take a few hours.
        3. Tap *Payments* and *Activate Payments*

        For more information on Signal Payments visit:

        https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""
        ).strip()

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


tip_message = """
If you like Imogen's art, you can show your support by donating within Signal Payments.
Send Imogen a message with the command "/tip" for donation instructions.  Every time she creates an image, it costs $0.09
Imogen shares tips with collaborators! If you like an Imogen Imoge, react ❤️  t️o it. When an Imoge gets multiple reactions, the person who prompted the Imoge will be awarded a tip (currently 0.1 MOB).
""".strip()


@dataclass
class Prompt:
    prompt: str
    elapsed_gpu: int = 0
    loss: float = 0.0
    author: str = ""
    signal_ts: int = -1
    group_id: str = ""
    version: str = ""


# async def check(req: web.request) -> web.Response:
#     bot = request.app.get("bot")
#     assert isinstance(bot, Imogen)
#     if not bot:
#         return web.Response(status=504, text="Sorry, no live workers.")
#     bot.send_message("/ping foo", +***REMOVED*** )


async def store_image_handler(  # pylint: disable=too-many-locals
    request: web.Request,
) -> web.Response:
    bot = request.app.get("bot")
    assert isinstance(bot, Imogen)
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
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
    prompt_id = int(request.query.get("id", "-1"))

    cols = ", ".join(Prompt.__annotations__)  # pylint: disable=no-member
    row = await bot.queue.execute(
        f"SELECT {cols} FROM prompt_queue WHERE id=$1", prompt_id
    )
    if not row or (not row[0].get("author") and not row[0].get("group_id")):
        await bot.admin("no prompt id found?", attachments=str(path))
        info = f"prompt id now found, sent {filename} sized of {size} to admin"
        logging.info(info)
        return web.Response(text=info)

    prompt = Prompt(**row[0])
    minutes, seconds = divmod(prompt.elapsed_gpu, 60)
    message = f"{prompt.prompt}\nTook {minutes}m{seconds}s to generate, "
    if prompt.loss:
        message += f"{prompt.loss} loss, "
    if prompt.version:
        message += f" v{prompt.version}."
    message += "\n\N{Object Replacement Character}"
    # needs to be String.length in Java, i.e. number of utf-16 code units,
    # which are 2 bytes each. we need to specify any endianness to skip
    # byte-order mark.
    mention_start = len(message.encode("utf-16-be")) // 2 - 1
    quote = (
        {
            "quote-timestamp": int(prompt.signal_ts),
            "quote-author": str(prompt.author),
            "quote-message": str(prompt.prompt),
            "mention": f"{mention_start}:1:{prompt.author}",
        }
        if prompt.author and prompt.signal_ts
        else {}
    )
    if prompt.group_id:
        rpc_id = await bot.send_message(
            None, message, attachments=[str(path)], group=prompt.group_id, **quote  # type: ignore
        )
        if random.random() < 0.05:
            asyncio.create_task(
                bot.send_message(None, tip_message, group=prompt.group_id)
            )
    else:
        rpc_id = await bot.send_message(
            prompt.author, message, attachments=[str(path)], **quote  # type: ignore
        )
        if prompt.author != utils.get_secret("ADMIN"):
            asyncio.create_task(
                bot.admin(message + f"author: {prompt.author}", attachments=[str(path)])
            )

    result = await bot.pending_requests[rpc_id]
    await bot.queue.execute(
        "UPDATE prompt_queue SET sent_ts=$1 WHERE id=$2",
        result.timestamp,
        prompt_id,
    )

    info = f"{filename} sized of {size} sent"
    logging.info(info)
    return web.Response(text=info)


app.add_routes([web.post("/attachment", store_image_handler)])
app.add_routes([])


if __name__ == "__main__":
    run_bot(Imogen, app)
