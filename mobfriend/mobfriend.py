#!/usr/bin/python3.9
import ast
import asyncio
import logging
from decimal import Decimal
from typing import Any, Optional

import pyqrcode
from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary

import mc_util
from forest import utils
from forest.core import Message, PayBot, Response, app, hide, requires_admin
from mc_util import mob2pmob, pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class MobFriend(PayBot):
    no_repay: list[str] = []
    exchanging_gift_code: list[str] = []

    async def handle_message(self, message: Message) -> Response:
        return await super().handle_message(message)

    async def do_makegift(self, msg: Message) -> Response:
        """
        /makegift
        I'll use your next payment to make a MobileCoin Gift Code that can be redeemed in other wallets.
        Be sure to include an extra 0.0008 MOB to pay the network fees!"""
        if msg.source in self.exchanging_gift_code:
            self.exchanging_gift_code.remove(msg.source)
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that gift code."
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)
        if msg.source not in self.exchanging_gift_code:
            self.exchanging_gift_code.append(msg.source)
        return "Your next transaction will be converted into a MobileCoin Gift Code that can be redeemed in other wallets.\nBe sure to include an extra 0.0008MOB to pay the network fees!"

    async def do_tip(self, msg: Message) -> Response:
        """
        /tip
        Records the next payment as a tip, not intended to make a giftcode, or as an accident."""
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)
        if msg.source in self.exchanging_gift_code:
            self.exchanging_gift_code.remove(msg.source)
        return "Your next transaction will be a tip, not refunded!\nThank you!\n(/no_tip cancels)"

    @hide
    async def do_no_tip(self, msg: Message) -> Response:
        """Cancels a tip in progress."""
        if msg.source in self.exchanging_gift_code:
            self.exchanging_gift_code.remove(msg.source)
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
    @hide
    async def do_pay(self, msg: Message) -> Response:
        if msg.arg1:
            payment_notif_sent = await self.send_payment(
                msg.source, mob2pmob(Decimal(msg.arg1))
            )
        else:
            payment_notif_sent = await self.send_payment(msg.source, mob2pmob(0.001))
        if payment_notif_sent:
            logging.info(payment_notif_sent)
            delta = (payment_notif_sent.timestamp - msg.timestamp) / 1000
            await self.admin(f"payment delta: {delta}")
            self.auxin_roundtrip_latency.append((msg.timestamp, "payment", delta))
        return None

    @time(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        if msg.source in self.exchanging_gift_code:
            resp = await self.build_gift_code(amount_pmob - FEE)
            self.exchanging_gift_code.remove(msg.source)
            if msg.source in self.no_repay:
                self.no_repay.remove(msg.source)
            return resp
        if msg.source not in self.no_repay:
            payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
            if not payment_notif:
                return None
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return None
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
        return f"Received {str(pmob2mob(amount_pmob)).rstrip('0')}MOB. Thank you for the tip!"

    @requires_admin
    async def do_eval(self, msg: Message) -> Response:
        """Evaluates a few lines of Python. Preface with "return" to reply with result."""

        async def async_exec(stmts: str, env: Optional[dict]) -> Any:
            parsed_stmts = ast.parse(stmts)
            fn_name = "_async_exec_f"
            fn = f"async def {fn_name}(): pass"
            parsed_fn = ast.parse(fn)
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
                .replace("/eval", "", 1)
                .lstrip(" ")
            )
            if source_blob:
                return str(await async_exec(source_blob, locals()))
        return None

    @requires_admin
    async def do_balance(self, _: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"

    @hide
    async def do_check_balance(self, msg: Message) -> Response:
        if not msg.arg1:
            return "/check_balance [gift code b58]"
        status = await self.mobster.req_(
            "check_gift_code_status", gift_code_b58=msg.arg1
        )
        pmob = int(status.get("result", {}).get("gift_code_value")) - FEE
        if not pmob:
            return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"
        mob_amt = pmob2mob(pmob)
        claimed = status.get("result", {}).get("gift_code_status", "")
        memo = status.get("result", {}).get("gift_code_memo") or "None"
        if "Claimed" in claimed:
            return "This gift code has already been redeemed!"
        return f"Gift code can be redeemed for {(mob_amt).quantize(Decimal('1.0000'))}MOB. ({pmob} picoMOB)\nMemo: {memo}"

    async def do_check(self, msg: Message) -> Response:
        """
        /check [base58 code]
        Helps identify a b58 code. If it's a gift code, it will return the balance."""
        if not msg.arg1:
            return "/do_check [base58 code]"
        status = await self.mobster.req_("check_b58_type", b58_code=msg.arg1)
        if status and status.get("result", {}).get("b58_type") == "PaymentRequest":
            status["result"]["data"]["type"] = "PaymentRequest"
            status["result"]["data"]["value"] = str(
                pmob2mob(status["result"]["data"]["value"])
            )
            return status.get("result", {}).get("data")
        if status and status.get("result", {}).get("b58_type") == "TransferPayload":
            return await self.do_check_balance(msg)
        return status.get("result")

    @hide
    async def do_create_payment_request(self, msg: Message) -> Response:
        """
        /create_payment_request [amount] [memo]

        Creates a payment request (as QR code and b58 code to copy and paste.)
        For example, /payme 1.0 "Pay me a MOB!"
        will create a payment request with
            * the memo "Pay me a MOB!",
            * a 1MOB value,
            * and the address of the requester's Signal account."""
        address = await self.get_address(msg.source)
        if not address:
            return "Unable to retrieve your MobileCoin address!"
        payload = mc_util.printable_pb2.PrintableWrapper()
        address_proto = mc_util.b58_wrapper_to_protobuf(address).public_address
        payload.payment_request.public_address.CopyFrom(address_proto)
        if msg.tokens and not (
            isinstance(msg.tokens[0], str)
            and len(msg.tokens) > 0
            and isinstance(msg.tokens[0], str)
            and msg.tokens[0].replace(".", "0", 1).isnumeric()
        ):
            return "Sorry, you need to provide a price (in MOB)!"
        if msg.arg1:
            payload.payment_request.value = mob2pmob(float(msg.arg1))
        if msg.tokens and len(msg.tokens) > 1:
            payload.payment_request.memo = " ".join(msg.tokens[1:])
        payment_request_b58 = mc_util.add_checksum_and_b58(payload.SerializeToString())
        pyqrcode.QRCode(payment_request_b58).png(
            f"/tmp/{msg.timestamp}.png", scale=5, quiet_zone=10
        )
        await self.send_message(
            recipient=msg.source,
            attachments=[f"/tmp/{msg.timestamp}.png"],
            msg="Scan me in the Mobile Wallet!",
        )
        return payment_request_b58

    do_payme = hide(do_create_payment_request)

    async def do_qr(self, msg: Message) -> Response:
        """
        /qr [gift card, url, etc]
        Creates a basic QR code for the provided content."""
        if not (msg.tokens and len(msg.tokens)):
            return "Usage: /qr [gift card, url, etc]"
        payload = " ".join(msg.tokens)
        pyqrcode.QRCode(payload).png(
            f"/tmp/{msg.timestamp}.png", scale=5, quiet_zone=10
        )
        await self.send_message(
            recipient=msg.source,
            attachments=[f"/tmp/{msg.timestamp}.png"],
            msg="Scan me!",
        )
        return None

    @requires_admin
    async def do_fsr(self, msg: Message) -> Response:
        """
        Make a request to the Full-Service instance behind the bot. Admin-only.
        ie) /fsr [command] ([arg1] [val1]( [arg2] [val2])...)"""
        if not msg.tokens:
            return "/fsr [command] ([arg1] [val1]( [arg2] [val2]))"
        if len(msg.tokens) == 1:
            return await self.mobster.req(dict(method=msg.tokens[0]))
        if (len(msg.tokens) % 2) == 1:
            fsr_command = msg.tokens[0]
            fsr_keys = msg.tokens[1::2]
            fsr_values = msg.tokens[2::2]
            params = dict(zip(fsr_keys, fsr_values))
            return str(await self.mobster.req_(fsr_command, **params))
        return "/fsr [command] ([arg1] [val1]( [arg2] [val2])...)"

    @hide
    async def do_echo(self, msg: Message) -> Response:
        """Returns a representation of the input message for debugging parse errors."""
        return msg.blob

    @hide
    async def do_printerfact(self, _: Message) -> str:
        """Learn a fact about something."""
        async with self.client_session.get(utils.get_secret("FACT_SOURCE")) as resp:
            fact = await resp.text()
            return fact.strip()

    async def do_claim(self, msg: Message) -> Response:
        """
        /claim [base58 gift code]
        Claims a gift code! Redeems a provided code to the bot's wallet and sends the redeemed balance."""
        if not msg.arg1:
            return "/claim [base58 gift code]"
        check_status = await self.mobster.req_(
            "check_gift_code_status", gift_code_b58=msg.arg1
        )
        amount_pmob = check_status.get("result", {}).get("gift_code_value")
        claimed = check_status.get("result", {}).get("gift_code_status", "")
        status = await self.mobster.req_(
            "claim_gift_code",
            gift_code_b58=msg.arg1,
            account_id=await self.mobster.get_account(),
        )
        if "Claimed" in claimed:
            return "Sorry, that gift code has already been redeemed!"
        if amount_pmob:
            await self.send_payment(
                msg.source, amount_pmob - FEE, "Gift code has been redeemed!"
            )
            amount_mob = pmob2mob(amount_pmob - FEE).quantize(Decimal("1.0000"))
            return f"Claimed a gift code containing {amount_mob}MOB.\nTransaction ID: {status.get('result', {}).get('txo_id')}"
        return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"

    do_redeem = hide(do_claim)


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = MobFriend()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
