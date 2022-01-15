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
    Message,
    PayBot,
    Response,
    UserError,
    app,
    hide,
    requires_admin,
    run_bot,
)


@dataclass
class InsertedPrompt:
    prompt: str
    author: str
    signal_ts: int
    group: str
    params: dict
    url: str = utils.URL

    def as_args(self) -> list:
        return [
            self.prompt,
            self.author,
            self.signal_ts,
            self.group,
            json.dumps(self.params),
            self.url,
        ]


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
    enqueue_free="SELECT enqueue_free_prompt(prompt:=$1, _author:=$2, signal_ts:=$3, group_id:=$4, params:=$5, url:=$6)",
    enqueue_paid="SELECT enqueue_paid_prompt(prompt:=$1, author:=$2, signal_ts:=$3, group_id:=$4, params:=$5, url:=$6)",
    length="SELECT count(id) AS len FROM {self.table} WHERE status='pending' OR status='assigned';",
    paid_length="SELECT count(id) AS len FROM {self.table} WHERE status='pending' OR status='assigned' AND paid=true;",
    list_queue="SELECT prompt FROM {self.table} WHERE status='pending' OR status='assigned' ORDER BY signal_ts ASC",
    react="UPDATE {self.table} SET reaction_map = reaction_map || $2::jsonb WHERE sent_ts=$1;",
    last_active_group="SELECT group_id FROM prompt_queue WHERE author=$1 AND group_id<>'' ORDER BY id DESC LIMIT 1",
)

openai.api_key = utils.get_secret("OPENAI_API_KEY")


async def get_output(cmd: str, inp: str = "") -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdin=-1, stdout=-1, stderr=-1)
    stdout, stderr = await proc.communicate(inp.encode())
    return stdout.decode().strip() or stderr.decode().strip()


if not utils.LOCAL:
    kube_cred = utils.get_secret("KUBE_CREDENTIALS")
    if kube_cred:
        logging.info("kube creds")
        Path("/root/.kube").mkdir(exist_ok=True, parents=True)
        open("/root/.kube/config", "w").write(base64.b64decode(kube_cred).decode())
    else:
        logging.info("couldn't find kube creds")

worker_status = "kubectl get pods -o json| jq '.items[] | {(.metadata.name): .status.phase}' | jq -s add"
jobcount = r"kubectl get jobs --no-headers | awk '$2 ~ /0\/1/ && $1 ~ /paid/ {print $1}' | wc -l"


password, rest = utils.get_secret("REDIS_URL").removeprefix("redis://:").split("@")
host, port = rest.split(":")
redis = aioredis.Redis(host=host, port=int(port), password=password)


messages = dict(
    no_credit="""You have no credit to submit priority requests.
    Please sent Imogen a payment, or message Imogen with the /credit command to learn how to add credit for priority features
    """,
    rate_limit="Slow down",
    tip_message="""
    If you like Imogen's art, you can show your support by donating within Signal Payments.
    Send Imogen a message with the command "/tip" for donation instructions.  Every time she creates an image, it costs $0.09
    Imogen shares tips with collaborators! If you like an Imogen Imoge, react ❤️  t️o it. When an Imoge gets multiple reactions, the person who prompted the Imoge will be awarded a tip (currently 0.1 MOB).
    """.strip(),
    activate_payments="""
    Thank you for collaborating with Imogen, if you'd like to support the project you can send her a tip of any amount with Signal Pay.

    If you get "This person has not activated payments", try messaging me with /ping. 

    If you have payments activated, simply click on the plus sign and choose payment.

    If you don't have Payments activated follow these instructions to activate it.

    1. Update Signal app: https://signal.org/install/
    2. Open Signal, tap on the icon in the top left for Settings. If you don’t see *Payments*, reboot your phone. It can take a few hours.
    3. Tap *Payments* and *Activate Payments*

    For more information on Signal Payments visit:

    https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments""",
)


class Imogen(PayBot):  # pylint: disable=too-many-public-methods
    worker_instance_id: Optional[str] = None

    async def start_process(self) -> None:
        self.queue = pghelp.PGInterface(
            query_strings=QueueExpressions,
            database=utils.get_secret("DATABASE_URL"),
        )
        await self.admin("\N{deciduous tree}\N{robot face}\N{hiking boot}")
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

    async def do_list_queue(self, _: Message) -> str:
        q = "; ".join(prompt.get("prompt") for prompt in await self.queue.list_queue())
        return q or "queue empty"

    do_list_prompts = do_listqueue = do_queue = hide(do_list_queue)

    @hide
    async def do_dump_queue(self, _: Message) -> Response:
        raise NotImplementedError

    async def do_workers(self, _: Message) -> Response:
        "shows worker state"
        return json.loads(await get_output(worker_status)) or "no workers running"

    async def do_balance(self, msg: Message) -> Response:
        if msg.group:
            return (
                "To make use of Imogen's paid features, please message Imogen directly."
            )
        balance = await self.get_user_balance(msg.source)
        prompts = int(balance / (self.image_rate_cents / 100))
        return f"Your current Imogen balance is ${balance:.2f} ({prompts} priority prompts)"

    image_rate_cents = 10

    async def payment_response(self, msg: Message, amount_pmob: int) -> str:
        # lookup last group the person submitted a prompt in
        # await self.queue.last_active_group(msg.source)
        # await self.send_message("Imogen got {amount}")
        rate = self.image_rate_cents / 100
        prompts = int(await self.mobster.pmob2usd(amount_pmob) / rate)
        total = int(await self.get_user_balance(msg.source) / rate)
        return f"You now have an additional {prompts} priority prompts. Total: {total}"

    async def ensure_free_worker(self) -> bool:
        # maybe check for the case of one running/completed pod and zero assigned workers
        # could actually try creating a new one each time and use the name conflict lol
        out = await get_output("kubectl create -f free-imagegen-job.yaml")
        if "AlreadyExists" in out:
            return False
        await self.admin("\N{rocket}\N{squared free}: " + out)
        return "error" not in out.lower()

    async def ensure_paid_worker(self, enqueue_result: dict) -> bool:
        queue_length = enqueue_result["queue_length"]
        workers = int(await get_output(jobcount))
        if workers == 0 or queue_length / workers > 5 and workers < 6:
            spec = open("paid-imagegen-job.yaml").read()
            with_name = spec.replace(
                "generateName: imagegen-job-paid-",
                f"name: imagegen-job-paid-{workers + 1}",
            )
            out = await get_output("kubectl create -f -", with_name)
            await self.admin("\N{rocket}\N{money with wings}: " + out)
            return True
        return False

    async def upload_attachment(self, msg: Message) -> dict[str, str]:
        if not msg.attachments:
            return {}
        attachment = msg.attachments[0]
        if (attachment.get("filename") or "").endswith(".txt"):
            raise UserError("your prompt is way too long")
        key = "input/" + attachment["id"] + "-" + (attachment.get("filename") or ".jpg")
        await redis.set(
            key, open(Path("./attachments") / attachment["id"], "rb").read()
        )
        return {"init_image": key}

    async def enqueue_prompt(
        self,
        msg: Message,
        params: dict,
        attachments: bool = False,
        paid: bool = False,
    ) -> str:
        if not msg.text.strip():
            return "A prompt is required"
        logging.info(msg.full_text)
        if attachments:
            params.update(await self.upload_attachment(msg))
        prompt = InsertedPrompt(
            prompt=msg.text,
            author=msg.source,
            signal_ts=msg.timestamp,
            group=msg.group or "",
            params=params,
        )
        if paid:
            result = (await self.queue.enqueue_paid(*prompt.as_args()))[0].get(
                "enqueue_paid_prompt"
            )
            logging.info(result)
            if not result.get("success"):
                return dedent(messages["no_credit"]).strip()
            worker_created = await self.ensure_paid_worker(result)
            priority = " priority"
        else:
            result = (await self.queue.enqueue_free(*prompt.as_args()))[0].get(
                "enqueue_free_prompt"
            )
            logging.info(result)
            if not result.get("success"):
                return dedent(messages["rate_limit"]).strip()
            worker_created = await self.ensure_free_worker()
            priority = ""
        if worker_created:
            deets = " (started a new worker)"
        else:
            deets = ""
        return f"you are #{result['queue_length']} in{priority} line{deets}"

    async def do_imagine(self, msg: Message) -> str:
        """/imagine [prompt]
        Generates an image based on your prompt.
        Request is handled in the free queue, every free request is addressed and generated sequentially.
        """
        return await self.enqueue_prompt(msg, {}, True, False)

    async def do_priority(self, msg: Message) -> str:
        """/imagine [prompt]
        Like /imagine but places your request on a priority queue. Priority items get dedicated workers and bypass the free queue.
        """
        return await self.enqueue_prompt(msg, {}, True, True)

    async def do_paint(self, msg: Message) -> str:
        """/paint <prompt>
        Generate an image using the WikiArt dataset and your prompt, generates painting-like images. Requests handled on the Free queue.
        """
        params = {
            "vqgan_config": "wikiart_16384.yaml",
            "vqgan_checkpoint": "wikiart_16384.ckpt",
        }
        return await self.enqueue_prompt(msg, params, False, False)

    async def do_priority_paint(self, msg: Message) -> str:
        """/paint <prompt>
        Generate an image using the WikiArt dataset and your prompt, generates painting-like images. Requests handled on the Free queue.
        """
        params = {
            "vqgan_config": "wikiart_16384.yaml",
            "vqgan_checkpoint": "wikiart_16384.ckpt",
        }
        return await self.enqueue_prompt(msg, params, False, True)

    def make_prefix(prefix: str) -> Callable:  # type: ignore  # pylint: disable=no-self-argument
        async def wrapped(self: "Imogen", msg: Message) -> str:
            msg.text = f"{prefix} {msg.text}"
            return await self.do_imagine(msg)

        wrapped.__doc__ = f"/{prefix} <prompt>: imagine it with {prefix} style"
        return wrapped

    do_dark_fantasy = make_prefix("dark fantasy")
    do_psychic = make_prefix("psychic")
    do_pastel = make_prefix("pastel")
    do_vibrant = make_prefix("vibrant")
    do_ukiyo = make_prefix("ukiyo")
    do_synthwave = make_prefix("synthwave")
    del make_prefix  # shouldn't be used after class definition is over

    # async def do_quick(self, msg: Message) -> str:
    #     """Generate a 512x512 image off from the last time this command was used"""
    #     return await self.enqueue_prompt(msg, {"feedforward": True}, False)

    # async def do_fast(self, msg: Message) -> str:
    #     """Generate an image in a single pass"""
    #     return await self.enqueue_prompt(msg, {"feedforward_fast": True}, False)

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
        return dedent(messages["activate_payments"]).strip()

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


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
                bot.send_message(None, messages["tip_message"], group=prompt.group_id)
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
