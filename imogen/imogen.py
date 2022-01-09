#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
# Copyright (c) 2021 Sylvie Liberman

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
from forest.core import (
    JSON,
    PayBot,
    Message,
    Response,
    app,
    hide,
    requires_admin,
    run_bot,
)

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
    paid_length="SELECT count(id) AS len FROM {self.table} WHERE status='pending' OR status='assigned' AND paid=true;",
    list_queue="SELECT prompt FROM {self.table} WHERE status='pending' OR status='assigned' ORDER BY signal_ts ASC",
    react="UPDATE {self.table} SET reaction_map = reaction_map || $2::jsonb WHERE sent_ts=$1;",
    workers="SELECT count(DISTINCT hostname) WHERE status='pending' OR status='uploading' ;",
)

openai.api_key = utils.get_secret("OPENAI_API_KEY")


async def get_output(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdout=-1, stderr=-1)
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip() or stderr.decode().strip()


if not utils.LOCAL:
    kube_cred = utils.get_secret("KUBE_CREDENTIALS")
    if kube_cred:
        logging.info("kube creds")
        Path("/root/.kube").mkdir(exist_ok=True, parents=True)
        open("/root/.kube/config", "w").write(base64.b64decode(kube_cred).decode())
    else:
        logging.info("couldn't find kube creds")

password, rest = utils.get_secret("REDIS_URL").removeprefix("redis://:").split("@")
host, port = rest.split(":")
redis = aioredis.Redis(host=host, port=int(port), password=password)

worker_status = "kubectl get pods -o json| jq '.items[] | {(.metadata.name): .status.phase}' | jq -s add"
podcount = "kubectl get pods --field-selector status.phase=Running --no-headers | wc -l"


class Imogen(PayBot):
    worker_instance_id: Optional[str] = None

    async def start_process(self) -> None:
        self.queue = pghelp.PGInterface(
            query_strings=QueueExpressions,
            database=utils.get_secret("DATABASE_URL"),
        )
        await self.admin("forestbot booting")
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

    async def do_workers(self, _: Message) -> Response:
        "shows worker state"
        return json.loads(await get_output(worker_status)) or "no workers running"

    async def do_balance(self, message: Message) -> Response:
        return f"${await self.get_user_balance(message.source)}"

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

    async def ensure_worker(self, paid: bool = False) -> bool:
        workers = int(await get_output(podcount))
        if workers == 0:
            out = await get_output("kubectl create -f free-imagegen-job.yaml")
            await self.admin("starting free worker: " + out)
            return True
        if not paid:
            logging.info("not paid and a worker already exists so not making a new one")
            return False
        paid_queue_size = (await self.queue.paid_length())[0][0]
        if paid_queue_size / workers > 5 and workers < 6:
            spec = open("paid-imagegen-job.yaml").read()
            with_name = spec.replace("generateName: imagegen-job-paid-", f"name: imagegen-job-paid-{workers + 1}")
            out = await get_output("kubectl create -f -", with_name)
            await self.admin("starting paid worker: " + out)
            return True
        return False

    async def do_imagine(self, msg: Message) -> str:
        """/imagine [prompt]"""
        if not msg.text.strip() and not msg.attachments:
            return "A prompt is required"
        logging.info(msg.full_text)
        params: JSON = {}
        if msg.attachments:
            attachment = msg.attachments[0]
            if (attachment.get("filename") or "").endswith(".txt"):
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
        paid = await self.get_user_balance(msg.source) > self.image_rate_cents / 100
        if paid:
            # maybe set memo to prompt_id in the sql or smth
            await self.mobster.ledger_manager.put_usd_tx(
                msg.source, -self.image_rate_cents, "image"
            )
        await self.queue.execute(
            """INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url) VALUES ($1, $2, $3, $4, $5, $6, $7);""",
            msg.text,
            paid,
            msg.source,
            msg.timestamp,
            msg.group,
            json.dumps(params),
            utils.URL,
        )
        worker_created = await self.ensure_worker(paid=paid)
        queue_length = (await self.queue.length())[0].get("len")
        if paid and worker_created:
            deets = " (paid, started a new worker)"
        elif paid:
            deets = " (paid)"
        elif worker_created:
            deets = " (started a new worker)"
        else:
            deets = ""
        return f"you are #{queue_length} in line{deets}"

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

    @hide
    async def do_spitball(self, msg: Message) -> str:
        prompt = (
            "text prompts for a neural network that are aiming to be artistic, "
            'short descriptive phrases of bizarre, otherworldly scenes: "'
        )
        completion = openai.Completion.create(  # type: ignore
            engine="davinci",
            prompt=prompt,
            temperature=0.9,
            max_tokens=140,
            top_p=1,
            frequency_penalty=0.0,
            presence_penalty=0.6,
            stop=['"', "\n"],
        )
        prompt = completion["choices"][0]["text"].strip()
        msg.text = prompt
        resp = await self.do_imagine(msg)
        return resp.replace("you are", f'"{prompt}" is')

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

    async def payment_response(self, msg: Message, amount_pmob: int) -> str:
        rate = self.image_rate_cents / 100
        prompts = int(await self.mobster.pmob2usd(amount_pmob) / rate)
        total = int(await self.get_user_balance(msg.source) / rate)
        return f"You now have an additional {prompts} priority prompts. Total: {total}"

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
#     bot.send_message("/ping foo", +12406171657 )


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
        info = f"prompt id not found, sent {filename} sized of {size} to admin"
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
        if random.random() < 0.03:
            asyncio.create_task(
                bot.send_message(None, tip_message, group=prompt.group_id)
            )
    else:
        rpc_id = await bot.send_message(
            prompt.author, message, attachments=[str(path)], **quote  # type: ignore
        )
        if prompt.author != utils.get_secret("ADMIN"):
            admin_task = bot.admin(
                message
                + f"\nrequested by {prompt.author} in DMs. prompt id: {prompt_id}",
                attachments=[str(path)],
            )
            asyncio.create_task(admin_task)

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
