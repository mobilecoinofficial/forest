#!/usr/bin/python3.9
# Copyright (c) 2022 Sylvie Liberman
# Copyright (c) 2022 The Forest Team

import asyncio
import base64
import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from mimetypes import guess_extension
from textwrap import dedent
from typing import Any, Callable

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
    group_help_text,
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
        seed TEXT,
        loss FLOAT DEFAULT null,
        filepath TEXT DEFAULT null,
        version TEXT DEFAULT null,
        hostname TEXT DEFAULT null,
        url TEXT DEFAULT 'https://imogen-renaissance.fly.dev/',
        sent_ts BIGINT DEFAULT null,
        errors INTEGER DEFAULT 0,
        selector TEXT);""",
    enqueue_any="SELECT enqueue_prompt(prompt:=$1, _author:=$2, signal_ts:=$3, group_id:=$4, params:=$5, url:=$6)",
    enqueue_free="SELECT enqueue_free_prompt(prompt:=$1, _author:=$2, signal_ts:=$3, group_id:=$4, params:=$5, url:=$6)",
    enqueue_paid="SELECT enqueue_paid_prompt(prompt:=$1, author:=$2, signal_ts:=$3, group_id:=$4, params:=$5, url:=$6)",
    length="SELECT count(id) AS len FROM {self.table} WHERE status='pending' OR status='assigned';",
    paid_length="SELECT count(id) AS len FROM {self.table} WHERE status='pending' OR status='assigned' AND paid=true;",
    list_queue="SELECT prompt FROM {self.table} WHERE status='pending' OR status='assigned' ORDER BY signal_ts ASC",
    react="UPDATE {self.table} SET reaction_map = reaction_map || $2::jsonb WHERE sent_ts=$1 RETURNING reaction_map, prompt, author, id;",
    last_active_group="SELECT group_id FROM prompt_queue WHERE author=$1 AND group_id<>'' ORDER BY id DESC LIMIT 1",
    costs="""select
    (select 0.860*sum(elapsed_gpu)/3600.0 from prompt_queue where inserted_ts > (select min(inserted_ts) from prompt_queue where paid=true and author<>'+16176088864') and inserted_ts is not null and author<>'+16176088864') as cost,
    (select sum(amount_usd_cents/100.0) from imogen_ledger where amount_usd_cents>0 and account<>'+16176088864') as revenue;
    """,
)
# selectors: paid, free, a6000, esrgan, diffusion, +ffmpeg video streaming
#
# maybe trigger after insert on prompt_queue, check rules and update state machine with get_output(kubectl) as necessary
# secondary table of request_type -> condition for scaling with kubectl request -> logic to scale -> function to record status (denied, worker created, normal)

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
    no_credit="""
    You have no credit to submit priority requests.

    Priority requests cost $0.10 each, bypass the free queue, and get dedicated workers when available.

    Please sent Imogen a payment. You can learn more about sending payments with the /signalpay command.
    """,
    last_paid="""
    You balance has reached $0. Please re-up your support of Imogen by sending a payment. This will continue your premium membership and priority queuing!
    """,
    rate_limit="""
    You currently have the maximum number of free requests in the queue (6), to request another image please wait for one of your requests to be generated, or add credit to your Imogen balance.

    Message Imogen with /balance to see your balance and learn how to add credit.
    """,
    activate_payments="""
    You can use Signal Payments to tip Imogen and make use of the priority features.

    To attach a payment, do the following in a direct message:
    -Hit the Plus Sign
    -Select "Payment"
    -Type your payment amount and hit Pay

    You will receive a message from Imogen with your new balance. You can check your current balance with "/balance".

    If you write “Tip” in the notes section for the payment, the payment automatically gets allocated as a tip and doesn’t increase your Imogen balance.

    If you get "This person has not activated payments", try messaging me with /ping. 

    If you don't have Payments activated follow these instructions to activate it.

    1. Update Signal app: https://signal.org/install/
    2. Open Signal, tap on the icon in the top left for Settings. If you don’t see *Payments*, reboot your phone. It can take a few hours.
    3. Tap *Payments* and *Activate Payments*

    For more information on Signal Payments visit:

    https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments

    To top up your Mobilecoin balance, follow these instructions: https://mobilecoin.com/news/how-to-buy-mob-in-the-us
    """,
    rules="""1. please don't be mean to Imogen
2. no prompts that would get Imogen's twitter account banned
3. don't be mean to others
4. this service is provided at our expense and we reserve the right to remove anyone for any or no reason
5. don't message people out of the blue""",
    intro="""ask Imogen to make some art for you with /imagine [prompt describing an image]

you can attach initial images to prompts.
use /imagine prompt1 // prompt2 to create a video fading between the two prompts.

crossposted to twitter.com/dreambs3

rules:
1. please don't be mean to Imogen
2. no prompts that would get Imogen's twitter account banned
3. don't be mean to others
4. this service is provided at our expense and we reserve the right to remove anyone for any or no reason
5. don't message people out of the blue""",
)
# If you have the Signal Payments feature activated, you can send a few cents directly to the bot by clicking on her avatar, then the "+" button, and sending a payment.  0.1 MOB will buy you a few priority queue slots.  Not necessary to do, of course.
auto_messages = [
    """
    If you like Imogen's art, you can show your support by donating within Signal Payments.

    Send Imogen a message with the command "/tip" for donation instructions.

    Imogen shares tips with collaborators! If you like an Imoge, react t️o it. When an Imoge gets multiple reactions, the person who prompted the Imoge will be awarded a tip.
    """,
    """
    Want to skip the line? Imogen offers a priority queue for a cost of $0.10 per image.

    DM funds to Imogen as a Signal payment to become a premium user. You can tip Imogen with /tip [amnt].

    Just want to tip Imogen? Send Imogen a payment with the note set to "tip"
    """,
]


class Imogen(PayBot):  # pylint: disable=too-many-public-methods
    prompts: dict[str, str] = {}

    async def start_process(self) -> None:
        self.queue = pghelp.PGInterface(
            query_strings=QueueExpressions,
            database=utils.get_secret("DATABASE_URL"),
        )
        await self.admin("\N{deciduous tree}\N{robot face}\N{hiking boot}")
        await super().start_process()

        # profile = {
        #     "given-name": "imogen",
        #     "about": "imagine there's an imoge generated",
        #     "about-emoji": "\N{Artist Palette}",
        #     "family-name": "",
        # }

    async def handle_reaction(self, msg: Message) -> Response:
        await super().handle_reaction(msg)
        assert msg.reaction
        logging.info(
            "updating %s with %s",
            msg.reaction.ts,
            json.dumps({msg.source: msg.reaction.emoji}),
        )
        # reaction_map, author, prompt,
        react_result = await self.queue.react(
            msg.reaction.ts, json.dumps({msg.source: msg.reaction.emoji})
        )
        if not react_result:
            return
        prompt = react_result[0]
        current_reaction_count = len(json.loads(prompt.get("reaction_map", "{}")))
        prompt_author = prompt.get("author")
        if current_reaction_count == 6:
            if not prompt_author or prompt_author == self.bot_number:
                logging.info("reaction isn't to a prompt")
                return None
            message = f"\N{Object Replacement Character}, your prompt got {current_reaction_count} reactions. Congrats!"
            quote = {
                "quote-timestamp": msg.reaction.ts,
                "quote-author": self.bot_number,
                "quote-message": prompt.get("prompt"),
                "mention": f"0:1:{prompt_author}",
            }
            if msg.group:
                await self.send_message(None, message, group=msg.group, **quote)
            else:
                await self.send_message(msg.source, message, **quote)
        # if current_reaction_count in (2, 6):
        #     await self.admin(f"trying to pay {prompt_author}")
        #     await self.client_session.post(
        #         utils.get_secret("PURSE_URL") + "/pay",
        #         data={
        #             "destination": prompt_author,
        #             "amount": 0.01,
        #             "message": f'sent you a tip for your prompt "{prompt.get("prompt")}" getting {current_reaction_count} reactions',
        #             "prompt_id": prompt.get("id"),
        #         },
        #     )
        #     return None

    def match_command(self, msg: Message) -> str:
        if msg.full_text and msg.full_text.lower().startswith("computer"):
            logging.info("startswith computer")
            kept_length = len(
                msg.full_text.lower()
                .removeprefix("computer")
                .lstrip(", ")
                .removeprefix("please")
                .lstrip()
            )
            # re-parse the tokenization without the prefix
            msg.parse_text(msg.text[-kept_length:])
        return super().match_command(msg)

    async def do_status(self, _: Message) -> str:
        "shows queue size"
        queue_length = (await self.queue.length())[0].get("len")
        return f"queue size: {queue_length}"

    async def do_prefix(self, msg: Message) -> Response:
        assert msg.tokens and len(msg.tokens) >= 2
        prefix = msg.tokens[0]
        msg.arg0 = msg.tokens[1].lstrip("/")
        msg.tokens = msg.tokens[2:]
        msg.text = " ".join(msg.tokens)
        resp = await self.handle_message(msg)
        if resp and isinstance(resp, str):
            return prefix + " " + resp
        return resp

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

    @requires_admin
    async def do_costs(self, _: Message) -> str:
        return repr((await self.queue.costs())[0])

    async def do_balance(self, msg: Message) -> Response:
        "returns your Imogen balance in USD for priority requests and tips"
        balance = await self.get_user_balance(msg.source)
        prompts = int(balance / (self.image_rate_cents / 100))
        balance_msg = (
            f"Your current Imogen balance is {prompts} priority prompt credits"
        )
        if balance == 0:
            balance_msg += "\n\n To buy more credits, send Imogen some MobileCoin. Try /signalpay to learn more activating payments"
        # if msg.group:
        #     await self.send_message(msg.source, balance_msg)
        #     return (
        #         "To make use of Imogen's paid features, please message Imogen directly."
        #     )
        return balance_msg

    image_rate_cents = 10

    async def payment_response(self, msg: Message, amount_pmob: int) -> str:
        # lookup last group the person submitted a prompt in
        # await self.queue.last_active_group(msg.source)
        # await self.send_message("Imogen got {amount}")
        value = await self.mobster.pmob2usd(amount_pmob)
        if "tip" in msg.payment.get("note", "").lower():
            await self.mobster.ledger_manager.put_usd_tx(
                msg.source, -value * 100, msg.payment["note"]
            )
            return "Thank you for tipping Imogen! Your tip will be used to improve Imogen and shared with collaborators"
        rate = self.image_rate_cents / 100
        prompts = int(value / rate)
        total = int(await self.get_user_balance(msg.source) / rate)
        return f"Thank you for supporting Imogen! You now have an additional {prompts} priority prompts. Total: {total}. Your prompts will automatically get dedicated workers and bypass the free queue."

    async def ensure_unique_worker(
        self, yaml_path: str = "free-imagegen-job.yaml"
    ) -> bool:
        # maybe check for the case of one running/completed pod and zero assigned workers
        # try creating a new one each time and use the name conflict lol
        out = await get_output(f"kubectl create -f {yaml_path}")
        if "AlreadyExists" in out:
            return False
        free = "\N{squared free}" if "free" in yaml_path else ""
        await self.admin(f"\N{rocket}{free}: " + out)
        return "error" not in out.lower()

    async def ensure_paid_worker(self, enqueue_result: dict) -> bool:
        queue_length = enqueue_result["queue_length"]
        workers_in_db = enqueue_result["workers"]
        # how many assigned prompts?
        if workers_in_db and queue_length / workers_in_db < 5:
            return False
        # this checks for jobs that have been created, but the process has not started yet
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
        if "image" not in attachment.get("contentType", ""):
            raise UserError("attachment must be an image")
        fname = (
            attachment.get("filename")
            or guess_extension(attachment.get("contentType", ""))
            or ".jpg"
        )
        key = "input/" + attachment["id"] + "-" + fname
        await redis.set(
            key,
            open(Path("./attachments") / attachment["id"], "rb").read(),
            ex=7200,  # expires after 2h
        )
        return {"init_image": key}

    async def upload_all_attachments(self, msg: Message) -> dict[str, list[str]]:
        if not msg.attachments:
            return {}
        keys: list[str] = []
        for attachment in msg.attachments:
            if (attachment.get("filename") or "").endswith(".txt"):
                raise UserError("your prompt is way too long")
            if "image" not in attachment.get("contentType", ""):
                raise UserError("attachment must be an image")
            fname = (
                attachment.get("filename")
                or guess_extension(attachment.get("contentType", ""))
                or ".jpg"
            )
            key = "input/" + attachment["id"] + "-" + fname
            await redis.set(
                key,
                open(Path("./attachments") / attachment["id"], "rb").read(),
                ex=7200,
            )
            keys.append(key)
        return {"image_prompts": keys}

    async def do_upsample(self, msg: Message) -> str:
        if not msg.quote:
            return "Quote an image I sent to use this command"
        ret = await self.queue.execute(
            "SELECT filepath FROM prompt_queue WHERE sent_ts=$1", msg.quote.ts
        )
        if not (filepath := ret[0].get("filepath")):
            return "Sorry, I don't have that image saved for upsampling right now"
        slug = (
            filepath.removeprefix("output/")
            .removesuffix(".png")
            .removesuffix("/progress")
        )
        ret = await self.queue.execute(
            """INSERT INTO prompt_queue (prompt, author, group_id, signal_ts, url, selector, paid)
            VALUES ($1, $2, $3, $4, $5, 'ESRGAN', true)
            RETURNING (SELECT count(*) + 1 FROM prompt_queue WHERE
            selector='ESRGAN' AND status='pending' OR status='assigned') as queue_length;""",
            slug,  # f"https://mcltajcadcrkywecsigc.supabase.in/storage/v1/object/public/imoges/{slug}.png",
            msg.source,
            msg.group,
            msg.timestamp,
            utils.URL,
        )
        ret[0].get("queue_length")
        await self.ensure_unique_worker("esrgan-job.yaml")
        return f"you are #{ret[0]['queue_length']} in line"

    do_enhance = hide(do_upsample)

    async def enqueue_prompt(
        self,
        msg: Message,
        params: dict,
        attachments: str = "",
    ) -> str:
        if attachments != "target" and not msg.text.strip():
            return "A prompt is required"
        logging.info(msg.full_text)
        if attachments == "init":
            params.update(await self.upload_attachment(msg))
        elif attachments == "target":
            params.update(await self.upload_all_attachments(msg))
        if not msg.group:
            params["nopost"] = True
        prompt = InsertedPrompt(
            prompt=msg.text,
            author=msg.source,
            signal_ts=msg.timestamp,
            group=msg.group or "",
            params=params,
        )
        result = (await self.queue.enqueue_any(*prompt.as_args()))[0].get(
            "enqueue_prompt"
        )
        logging.info(result)
        if result.get("paid"):
            if not result.get("success"):
                return dedent(messages["no_credit"]).strip()
            worker_created = await self.ensure_paid_worker(result)
            if not result.get("balance_remaining"):
                asyncio.create_task(
                    self.send_message(msg.source, dedent(messages["last_paid"]).strip())
                )
            priority = " priority"
        else:
            if not result.get("success"):
                return dedent(messages["rate_limit"]).strip()
            worker_created = await self.ensure_unique_worker("free-imagegen-job.yaml")
            priority = ""
        if worker_created:
            deets = " (started a new worker)"
        else:
            deets = ""
        return f"you are #{result['queue_length']} in{priority} line{deets}"

    async def do_imagine(self, msg: Message) -> str:
        """
        /imagine [prompt]
        Generates an image based on your prompt.
        Request is handled in the free queue, every free request is addressed and generated sequentially.
        """
        return await self.enqueue_prompt(msg, {}, attachments="init")

    async def do_imagine_target(self, msg: Message) -> str:
        """
        /imagine [prompt]
        Generates an image based on your prompt.
        Request is handled in the free queue, every free request is addressed and generated sequentially.
        """
        return await self.enqueue_prompt(msg, {}, attachments="target")

    async def do_nopost(self, msg: Message) -> str:
        "Like /imagine, but doesn't post on Twitter"
        return await self.enqueue_prompt(msg, {"nopost": True}, attachments="init")

    @hide
    async def do_priority(self, msg: Message) -> str:
        return await self.do_imagine(msg)

    async def do_paint(self, msg: Message) -> str:
        """
        /paint <prompt>
        Generate an image using the WikiArt dataset and your prompt, generates painting-like images. Requests handled on the free queue.
        """
        params = {
            "vqgan_config": "wikiart_16384.yaml",
            "vqgan_checkpoint": "wikiart_16384.ckpt",
        }
        return await self.enqueue_prompt(msg, params, attachments="init")

    @hide
    async def do_priority_paint(self, msg: Message) -> str:
        """
        /priority_paint <prompt>
        Like /paint but places your request on the priority queue. Priority items get dedicated workers when available and bypass the free queue.
        """
        return await self.do_paint(msg)

    @hide
    async def do_highres(self, msg: Message) -> str:
        "Generate a 2626x1616 image. Costs 0.25 MOB"
        balance = await self.get_user_balance(msg.source)
        if not msg.text.strip():
            return "A prompt is required"
        if balance < 1.0:  # hosting cost is about 16/60 * 1.96 = 0.52
            return "Highres costs 0.25 MOB. Please send a payment to use this command."
        worker_created = await self.ensure_unique_worker("a6000.yaml")
        logging.info(msg.full_text)
        params: dict[str, Any] = {"size": [2620, 1610]}
        params.update(await self.upload_attachment(msg))
        if not msg.group:
            params["nopost"] = True
        raw_result = await self.queue.execute(
            """
            INSERT INTO prompt_queue (prompt, paid, author, signal_ts, group_id, params, url, selector)
            VALUES ($1, true, $2, $3, $4, $5, $6, 'a6000')
            RETURNING id AS prompt_id,
            (select count(*) from prompt_queue where
            (status='pending' or status='assigned') and selector='a6000') as queue_length;
            """,
            msg.text,
            msg.source,
            msg.timestamp,
            msg.group or "",
            json.dumps(params),
            utils.URL,
        )
        if not raw_result:
            return "Sorry, couldn't enqueue your prompt"
        result = raw_result[0]
        # note that this isn't atomic and it's possible to use this command twice and end up with a negative balance
        await self.mobster.ledger_manager.put_usd_tx(
            msg.sources, -100, str(result.get("prompt_id", ""))
        )
        logging.info(result)
        if worker_created:
            deets = " (started a new worker)"
        else:
            deets = ""
        return f"you are #{result['queue_length']} in the highres line{deets}"

    def make_prefix(prefix: str) -> Callable:  # type: ignore  # pylint: disable=no-self-argument
        async def wrapped(self: "Imogen", msg: Message) -> Response:
            if msg.group and msg.group == utils.get_secret("ADMIN_GROUP"):
                return None
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

    def single_response(response: str) -> Callable:  # type: ignore # pylint: disable=no-self-argument
        async def wrapped(self: "Imogen", msg: Message) -> Response:
            del self, msg  # shush pylint
            return response

        wrapped.__doc__ = f"says {response}"
        return wrapped

    do_rules = single_response(messages["rules"])
    do_intro = single_response(messages["intro"])
    do_beep = single_response("beep")
    do_poke = hide(single_response("poke"))

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
    async def do_ask(self, msg: Message) -> Response:
        if msg.text and msg.text[0] not in "/\N{Object Replacement Character}":
            return None
        prompt = (
            "The following is a conversation with an AI assistant. "
            "The assistant is helpful, creative, clever, funny, very friendly, an artist and anarchist\n\n"
            "Human: Hello, who are you?\nAI: My name is Imogen, I'm an AI that makes dream-like images. What's up?\n"
            f"Human: {msg.text}\nAI: "
        )
        response = openai.Completion.create(  # type: ignore
            engine="text-davinci-001",
            prompt=prompt,
            temperature=0.99,
            max_tokens=140,
            top_p=1,
            frequency_penalty=0.0,
            presence_penalty=0.6,
        )
        return response["choices"][0]["text"].strip()

    @hide
    async def do_instruct(self, msg: Message) -> str:
        response = openai.Completion.create(  # type: ignore
            engine="text-davinci-001",
            prompt=msg.text,
            temperature=0.99,
            max_tokens=140,
            top_p=1,
            frequency_penalty=0.0,
            presence_penalty=0.6,
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
            frequency_penalty=0.02,
            presence_penalty=0.6,
            stop=["\n", " Human:", " AI:"],
        )
        return response["choices"][0]["text"].strip()

    @hide
    async def do_answer(self, msg: Message) -> str:
        response = openai.Completion.create(  # type: ignore
            engine="text-davinci-001",
            prompt=msg.text,
            temperature=0,
            max_tokens=120,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
        )
        return response["choices"][0]["text"].strip()

    @hide
    async def do_spitball(self, msg: Message) -> str:
        "Spitball a prompt"
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

    @hide
    async def do_test(self, msg: Message) -> str:
        if msg.tokens and len(msg.tokens) == 1:
            msg.text = "a perfectly normal test image"
        return await self.enqueue_prompt(
            msg, {"size": [50, 50], "max_iterations": 5}, attachments="init"
        )

    @requires_admin
    async def do_exception(self, msg: Message) -> None:
        raise Exception("You asked for it~!")

    async def default(self, message: Message) -> Response:
        if message.full_text and message.full_text.startswith("/"):
            message.full_text = message.full_text.removeprefix("/")
            message.parse_text(message.full_text)
            return await self.do_imagine(message)
        return await super().default(message)

    @group_help_text(
        """
        To send imogen a tip, first send imogen a payment, and then you can use /tip [amnt] to tip Imogen from your balance.

        To send Imogen payments, please DM her on Signal and use the command /signalpay for instructions
        """
    )
    async def do_tip(self, msg: Message) -> str:
        """
        If you already have Imogen balance, you can use `/tip [amount]` to send that amount in USD as a tip to Imogen.

        You can also type `/tip all` To check your imogen balance, type /balance.

        To top up your balance, simply send Imogen a payment with Signal. For instructions on how to send payments with Signal, type /signalpay.
        """
        if msg.arg1 is None:
            return dedent(self.do_tip.__doc__).strip()
        balance = await self.get_user_balance(msg.source)
        if msg.arg1 and msg.arg1.lower() in ("all", "everything"):
            amount = balance
        else:
            try:
                amount = float((msg.arg1 or "").strip("$"))
                if amount < 0.01:
                    return "/tip requires amounts in USD"
                if amount > balance:
                    return "That's more than your balance"
            except ValueError:
                return f"Couldn't parse {msg.arg1} as an amount"
        await self.mobster.ledger_manager.put_usd_tx(msg.source, -amount * 100, "tip")
        return f"Thank you for tipping ${amount:.2f}"

    async def do_signalpay(self, msg: Message) -> Response:
        "Learn about sending payments on Signal"
        if msg.group:
            await self.send_message(
                msg.source, dedent(messages["activate_payments"]).strip()
            )
            return "To send Imogen a payment, please message Imogen directly."
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
    if not bot or not isinstance(bot, Imogen):
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
        if random.random() < 0.04:
            msg = dedent(random.choice(auto_messages)).strip()
            asyncio.create_task(bot.send_message(None, msg, group=prompt.group_id))
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


async def prompt_msg_handler(request: web.Request) -> web.Response:
    """Allow webhooks to send messages to users.
    Turn this off, authenticate, or obfuscate in prod to someone from using your bot to spam people
    """
    bot = request.app.get("bot")
    if not bot or not isinstance(bot, Imogen):
        return web.Response(status=504, text="Sorry, no live workers.")
    prompt_id = int(request.query.get("id", "-1"))
    cols = ", ".join(Prompt.__annotations__)  # pylint: disable=no-member
    row = await bot.queue.execute(
        f"SELECT {cols} FROM prompt_queue WHERE id=$1", prompt_id
    )
    if not row or (not row[0].get("author") and not row[0].get("group_id")):
        await bot.admin("no prompt id found?")
        info = "prompt id not found"
        logging.info(info)
        return web.Response(text=info)
    prompt = Prompt(**row[0])
    message = await request.text()
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
            None, message, group=prompt.group_id, **quote  # type: ignore
        )
    else:
        rpc_id = await bot.send_message(prompt.author, message, **quote)  # type: ignore
        if prompt.author != utils.get_secret("ADMIN"):
            admin_task = bot.admin(
                message
                + f"\nrequested by {prompt.author} in DMs. prompt id: {prompt_id}",
            )
            asyncio.create_task(admin_task)
    result = await bot.pending_requests[rpc_id]
    await bot.queue.execute(
        "UPDATE prompt_queue SET sent_ts=$1 WHERE id=$2",
        result.timestamp,
        prompt_id,
    )
    logging.info("upsampled sent")
    return web.Response(text="sent")


app.add_routes(
    [
        web.post("/attachment", store_image_handler),
        web.post("/prompt_message", prompt_msg_handler),
    ]
)

if __name__ == "__main__":
    run_bot(Imogen, app)
