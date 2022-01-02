#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import ast
import asyncio
import hashlib
import json
import logging
import os
from decimal import Decimal
from typing import Any, Optional

import aiohttp
import base58
from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary

import mc_util
from forest import utils
from forest.core import Message, PayBot, Response, app, hide, requires_admin
from mc_util import pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")

english_words = open("english.txt").read().splitlines()
from restricted_usernames import is_restricted

krng = open("/dev/urandom", "rb")

SALT = os.getenv("SALT", "ECmG8HtNNMWb4o2bzyMqCmPA6KTYJPCkd")
AUTH = os.getenv("XAUTH", "totallyAuthorized")


def random_username():
    return ".".join(
        sorted(english_words, key=lambda _: int.from_bytes(krng.read(4), "little"))[0:3]
    )


class KVStoreClient:
    def __init__(
        self,
        base_url: str = "https://kv.sometimes.workers.dev",
        auth_str: str = AUTH,
    ):
        self.url = base_url
        self.conn = aiohttp.ClientSession()
        self.auth = auth_str

    async def post(self, key: str, data: str, ttl_seconds: int = 600) -> str:
        kv_set_req = self.conn.post(
            f"{self.url}/{key}?ttl={ttl_seconds}&value={data}",
            headers={
                "Content-Type": "text/plain; charset=utf8",
                "X-AUTH": f"{self.auth}",
            },
        )
        async with kv_set_req as resp:
            return await resp.text()

    async def get(self, key: str) -> str:
        kv_get_req = self.conn.get(f"{self.url}/{key}")
        async with kv_get_req as resp:
            return await resp.text()


class BounceRegClient(KVStoreClient):
    async def check_email_by_username(self, maybe_username: str) -> Optional[str]:
        to_address = f"{maybe_username}@forest.contact"
        to_token = base58.b58encode(
            hashlib.sha256(f"{SALT}{to_address}".encode()).digest()
        ).decode()
        check = await self.get(to_token)
        if check == "EMPTY":
            return None
        return check

    async def assign_email_by_username(self, username: str, uuid: str, ttl: int = 600):
        to_address = f"{username}@forest.contact"
        to_token = base58.b58encode(
            hashlib.sha256(f"{SALT}{to_address}".encode()).digest()
        ).decode()
        return await self.post(to_token, uuid, ttl)


class BounceBot(PayBot):
    def __init__(self, kv_store: KVStoreClient = BounceRegClient()):
        self.kv_store = kv_store
        self.last_seen: dict[str, str] = {}
        self.no_repay: list[str] = []
        self.pending_orders: dict[str, str] = {}
        self.temporary: dict[str, str] = {}
        super().__init__()

    async def handle_message(self, message: Message) -> Response:
        user_last_seen = self.last_seen.get(message.source, 0)
        self.last_seen[message.source] = message.timestamp / 1000
        msg = message
        if (
            (msg.timestamp / 1000 - user_last_seen) > 600000 and msg.text
        ) or msg.command == "help":
            await self.send_message(
                msg.source,
                [
                    "Welcome to mail by forest.contact - privacy preserving receive-only emails.",
                    "If you would like a temporary email address to receive confirmation messages, reply 'temp' or 'temporary'",
                    "Emails sent to this address will be delivered as Signal messages for twenty-four hours.",
                    "If you would like a custom email address, use the 'register' command. For more information, try 'help register'",
                ],
            )
        return await super().handle_message(message)

    @hide
    async def do_temporary(self, msg: Message) -> Response:
        return await self.do_temp(msg)

    async def do_temp(self, msg: Message) -> Response:
        """
        temp
        Returns a temporary email good for twenty-four hours.
        """
        if msg.command and "temp" in msg.command:
            username = random_username()
            # if username is not taken
            if await self.kv_store.check_email_by_username(username) is None:
                # assign it for one day
                await self.kv_store.assign_email_by_username(
                    username, msg.uuid, ttl=60 * 60 * 24
                )
                self.temporary[msg.source] = username
                return [
                    f"You now can use {username}@forest.contact for the next twenty-four hours.",
                    "Send 0.1MOB to extend this address for the next year.",
                ]

    @hide
    async def do_shibboleth(self, msg: Message) -> Response:
        if utils.get_secret("ADMIN"):
            await self.send_message(
                utils.get_secret("ADMIN"),
                f"{msg.source} used shibboleth! It was very effective.",
            )
        amount_mob = 1.0
        if msg.source in self.pending_orders:
            if amount_mob >= 0.2:
                username = self.pending_orders.get(msg.source, "")
                if username:
                    res = await self.kv_store.assign_email_by_username(
                        username, msg.uuid, ttl=60 * 60 * 24 * 365
                    )
                    if res == "OK":
                        self.pending_orders.pop(msg.source)
                        await self.send_message(
                            msg.source,
                            f"Thank you for your crypticurrency payment. You have registered {username}@forest.contact for one year.",
                        )
        if msg.source in self.temporary:
            if amount_mob >= 0.1:
                username = self.temporary.get(msg.source, "")
                if username:
                    res = await self.kv_store.assign_email_by_username(
                        username, msg.uuid, ttl=60 * 60 * 24 * 365
                    )
                    if res == "OK":
                        self.temporary.pop(msg.source)
                        await self.send_message(
                            msg.source,
                            f"Thank you for your crypticurrency payment. You have registered {username}@forest.contact for one year.",
                        )
        return

    async def do_register(self, msg: Message) -> Response:
        """
        register [username]
        register alice
            > Please pay 0.2 MOB to register alice@forest.contact for the next year!
        register admin
            > We're sorry, admin@forest.contact is not available!
        """
        maybe_username = msg.arg1
        if maybe_username:
            check = await self.kv_store.check_email_by_username(maybe_username)
            if check is None and not is_restricted(maybe_username):
                self.pending_orders[msg.source] = maybe_username
                return f"Please pay 0.2 MOB to register {maybe_username}@forest.contact for one year."
            return f"We're sorry, {maybe_username} is not available!"
        return "Usage: '/register [email_handle]'. Try '/help register' for more info!"

    async def do_tip(self, msg: Message) -> Response:
        """
        /tip
        Records the next payment as a tip, not intended to make a payment."""
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)
        return "Your next transaction will be a tip, not refunded!\nThank you!\n(/cancel cancels)"

    @hide
    async def do_cancel(self, msg: Message) -> Response:
        """Cancels a tip in progress."""
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that tip."
        return "Couldn't find a tip in process to cancel!"

    @hide
    @requires_admin
    async def do_exception(self, _: Message) -> None:
        raise Exception("You asked for it!")

    @hide
    @requires_admin
    async def do_wait(self, _: Message) -> str:
        await asyncio.sleep(60)
        return "waited!"

    @time(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        amount_mob = float(mc_util.pmob2mob(amount_pmob).quantize(Decimal("1.0000")))
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
            return f"Received {str(pmob2mob(amount_pmob)).rstrip('0')}MOB. Thank you for the tip!"
        if msg.source in self.pending_orders and amount_mob >= 0.2:
            username = self.pending_orders.get(msg.source, "")
            if username:
                res = await self.kv_store.assign_email_by_username(
                    username, msg.uuid, ttl=60 * 60 * 24 * 365
                )
                if res == "OK":
                    self.pending_orders.pop(msg.source)
                    return f"Thank you for your {amount_mob} MOB payment. You have registered {username}@forest.contact for one year."
                return res
        if msg.source in self.temporary and amount_mob >= 0.1:
            username = self.temporary.get(msg.source, "")
            res = await self.kv_store.assign_email_by_username(
                username, msg.uuid, ttl=60 * 60 * 24 * 365
            )
            if res == "OK":
                self.temporary.pop(msg.source)
                return f"Thank you for your {amount_mob} MOB payment. You have registered {username}@forest.contact for one year."
            return res
        if msg.source not in self.no_repay:
            payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
            if not payment_notif:
                return None
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return "We have refunded your accidental payment, minus fees!"

    @hide
    @requires_admin
    async def do_eval(self, msg: Message) -> Response:
        """Evaluates a few lines of Python. Preface with "return" to reply with result."""

        async def async_exec(stmts: str, env: Optional[dict]) -> Any:
            parsed_stmts = ast.parse(stmts)
            fn_name = "_async_exec_f"
            my_fn = f"async def {fn_name}(): pass"
            parsed_fn = ast.parse(my_fn)
            for node in parsed_stmts.body:
                ast.increment_lineno(node)
            assert isinstance(parsed_fn.body[0], ast.AsyncFunctionDef)
            parsed_fn.body[0].body = parsed_stmts.body
            code = compile(parsed_fn, filename="<ast>", mode="exec")
            exec(code, env)  # pylint: disable=exec-used
            return await eval(f"{fn_name}()", env)  # pylint: disable=eval-used

        if msg.tokens and len(msg.tokens):
            source_blob = (
                msg.blob.get("content", {})
                .get("text_message", "")
                .replace("eval", "", 1)
                .replace("Eval", "", 1)
                .lstrip(" ")
                .rstrip("/")
            )
            if source_blob:
                return str(await async_exec(source_blob, locals()))
        return None

    @requires_admin
    async def do_balance(self, _: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"

    async def do_printerfact(self, _: Message) -> str:
        """Learn a fact about something."""
        async with self.client_session.get(utils.get_secret("FACT_SOURCE")) as resp:
            fact = await resp.text()
            return fact.strip()


async def inbound_sms_handler(request: web.Request) -> web.Response:
    """Handles SMS messages received by our numbers.
    Try groups, then try users, otherwise fall back to an admin
    """
    bot = request.app.get("bot")
    msg_data: dict[str, str] = json.loads(await request.text())  # type: ignore
    to_address = msg_data.get("To").split("<", 1)[-1].split(">", 1)[0]
    to_token = base58.b58encode(
        hashlib.sha256(f"{SALT}{to_address}".encode()).digest()
    ).decode()
    maybe_recipient = await request.app.get("kv_client").get(to_token)
    isuuid = False
    try:
        nodashes = maybe_recipient.replace("-", "", 4)
        if len(nodashes) == 32 and int(nodashes, base=16):
            isuuid = True
    except ValueError:
        pass
    if isuuid:
        await bot.send_message(maybe_recipient, msg_data)
    return web.Response(text="TY!")


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])
    app.add_routes([web.post("/inbound", inbound_sms_handler)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = BounceBot(kv_store=BounceRegClient())
        out_app["kv_client"] = KVStoreClient()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
