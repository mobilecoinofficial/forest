#!/usr/bin/python3.9
import logging
import mc_util

import asyncio
import pyqrcode

from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary

from forest.core import PayBot, Message, Response, app, requires_admin, hide
from forest import utils
from mc_util import pmob2mob, mob2pmob
from decimal import Decimal

FEE = int(1e12 * 0.0004)

REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class MobFriend(PayBot):
    no_repay: list[str] = []
    exchanging_cash_code: list[str] = []

    async def handle_message(self, message: Message) -> Response:
        return await super().handle_message(message)

    async def do_makegift(self, msg: Message) -> Response:
        if msg.source in self.exchanging_cash_code:
            self.exchanging_cash_code.remove(msg.source)
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that cash code."
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)
        if msg.source not in self.exchanging_cash_code:
            self.exchanging_cash_code.append(msg.source)
        return "Your next transaction will be converted into a MobileCoin Cash Code that can be redeemed in other wallets.\nBe sure to include an extra 0.0008MOB to pay the network fees!"

    async def do_tip(self, msg: Message) -> Response:
        """Records the next payment as a tip, not intended to make a giftcode, or as an accident."""
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)

        if msg.source in self.exchanging_cash_code:
            self.exchanging_cash_code.remove(msg.source)
        return "Your next transaction will be a tip, not refunded!\nThank you!\n(/no_tip cancels)"

    @hide
    async def do_no_tip(self, msg: Message) -> Response:
        """Cancels a tip in progress."""
        if msg.source in self.exchanging_cash_code:
            self.exchanging_cash_code.remove(msg.source)
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that tip."
        return "Couldn't find a tip in process to cancel!"

    @hide
    async def do_exception(self, _: Message):
        raise Exception("You asked for it!")
        return None

    @hide
    async def do_wait(self, _: Message):
        await asyncio.sleep(60)
        return "waited!"

    @time(REQUEST_TIME)  # type: ignore
    @hide
    async def do_pay(self, msg: Message) -> Response:
        if msg.arg1:
            payment_notif_sent = await self.send_payment(
                msg.source, mob2pmob(Decimal(msg.arg1))  # type: ignore
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
        if msg.source in self.exchanging_cash_code:
            await self.build_cash_code(msg.source, amount_pmob - FEE)
            self.exchanging_cash_code.remove(msg.source)
            if msg.source in self.no_repay:
                self.no_repay.remove(msg.source)
            return None
        elif msg.source not in self.no_repay:
            payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
            if not payment_notif:
                return None
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return None
        else:
            if msg.source in self.no_repay:
                self.no_repay.remove(msg.source)
            return f"Received {str(pmob2mob(amount_pmob)).rstrip('0')}MOB. Thank you for the tip!"

    @requires_admin
    async def do_eval(self, msg: Message) -> Response:
        """Evaluates a few lines of Python. Preface with "return" to reply with result."""
        import ast

        async def async_exec(stmts, env=None):  # type: ignore
            parsed_stmts = ast.parse(stmts)
            fn_name = "_async_exec_f"
            fn = f"async def {fn_name}(): pass"
            parsed_fn = ast.parse(fn)
            for node in parsed_stmts.body:
                ast.increment_lineno(node)
            parsed_fn.body[0].body = parsed_stmts.body  # type: ignore
            exec(compile(parsed_fn, filename="<ast>", mode="exec"), env)
            return await eval(f"{fn_name}()", env)

        if msg.tokens and len(msg.tokens):
            return str(await async_exec(" ".join(msg.tokens), locals()))  # type: ignore
        return None

    @requires_admin
    async def do_balance(self, msg: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"  # type: ignore

    @hide
    async def do_check_balance(self, msg: Message) -> Response:
        if msg.arg1:
            status = await self.mobster.req_(
                "check_gift_code_status", gift_code_b58=msg.arg1
            )
            pmob = Decimal(status.get("result", {}).get("gift_code_value")) - Decimal(
                FEE
            )
            if pmob:
                mob_amt = pmob2mob(pmob)  # type: ignore
                claimed = status.get("result", {}).get("gift_code_status", "")
                memo = status.get("result", {}).get("gift_code_memo") or "None"
                if "Claimed" in claimed:
                    return "This gift code has already been redeemed!"
                return f"Gift code can be redeemed for {(mob_amt-Decimal(0.0004)).quantize(Decimal('1.0000'))}MOB. ({pmob} picoMOB)\nMemo: {memo}"
            else:
                return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"
        else:
            return "/check_balance <b58>"

    async def do_check(self, msg: Message) -> Response:
        """ Helps identify a b58 code. If it's a gift code, it will return the balance. """
        if not msg.arg1:
            return "/check_b58_type <b58>"
        status = await self.mobster.req_("check_b58_type", b58_code=msg.arg1)
        if status and status.get("result", {}).get("b58_type") == "PaymentRequest":
            status["result"]["data"]["type"] = "PaymentRequest"
            status["result"]["data"]["value"] = str(
                pmob2mob(status["result"]["data"]["value"])
            )
            return status.get("result", {}).get("data")
        elif status and status.get("result", {}).get("b58_type") == "TransferPayload":
            return await self.do_check_balance(msg)
        else:
            return status.get("result")

    @hide
    async def do_create_payment_request(self, msg: Message) -> Response:
        """Creates a payment request (as QR code and b58 code to copy and paste.)
        ie) /payme 1.0 "Pay me a MOB!"
        will create a payment request with
            * the memo "Pay me a MOB!",
            * a 1MOB value,
            * and the address of the requester's Signal account."""
        address = await self.get_address(msg.source)
        if not address:
            return "Unable to retrieve your MobileCoin address!"
        payload = mc_util.printable_pb2.PrintableWrapper()
        payload.payment_request.public_address.CopyFrom(
            mc_util.b58_wrapper_to_transfer_payload(address).public_address
        )
        if msg.tokens and not (
            isinstance(msg.tokens[0], str)
            and len(msg.tokens) > 0
            and isinstance(msg.tokens[0], str)
            and msg.tokens[0].replace(".", "0", 1).isnumeric()
        ):
            return "Sorry, you need to provide a price (in MOB)!"
        if msg.tokens and len(msg.tokens):
            payload.payment_request.value = mob2pmob(float(msg.tokens[0]))
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

    do_payme = do_create_payment_request

    async def do_qr(self, msg: Message) -> Response:
        """Creates a basic QR code for the provided content."""
        if msg.tokens and len(msg.tokens):
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
        else:
            return "Usage: /qr <value>"

    @requires_admin
    async def do_fsr(self, msg: Message) -> Response:
        """Make a request to the Full-Service instance behind the bot. Admin-only.
        ie) /fsr <command> (<arg1> <val1>( <arg2> <val2>)...)"""
        if not msg.tokens or not len(msg.tokens):
            return "/fsr <command> (<arg1> <val1>( <arg2> <val2>))"
        if len(msg.tokens) == 1:
            return await self.mobster.req(dict(method=msg.tokens[0]))
        elif (len(msg.tokens) % 2) == 1:
            fsr_command = msg.tokens[0]
            fsr_keys = msg.tokens[1::2]
            fsr_values = msg.tokens[2::2]
            params = {k: v for (k, v) in zip(fsr_keys, fsr_values)}
            return str(await self.mobster.req_(fsr_command, **params))
        else:
            return "/fsr <command> (<arg1> <val1>( <arg2> <val2>)...)"

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
        """Claims a gift code! Redeems a provided code to the bot's wallet and sends the redeemed balance."""
        if msg.arg1:
            status = await self.mobster.req_(
                "check_gift_code_status", gift_code_b58=msg.arg1
            )
            amount_pmob = status.get("result", {}).get("gift_code_value")
            claimed = status.get("result", {}).get("gift_code_status", "")
            status = await self.mobster.req_(
                "claim_gift_code",
                gift_code_b58=msg.arg1,
                account_id=await self.mobster.get_account(),
            )
            if amount_pmob and "Claimed" not in claimed:
                payment_notif = await self.send_payment(
                    msg.source, amount_pmob - FEE, "Gift code has been redeemed!"
                )
                amount_mob = pmob2mob(amount_pmob - FEE).quantize(Decimal("1.0000"))
                return f"Claimed a giftcode containing {amount_mob}MOB.\nTransaction ID: {status.get('result', {}).get('txo_id')}"
            elif "Claimed" in claimed:
                return "Sorry, that giftcode has already been redeemed!"
            else:
                return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"
        else:
            return "/claim <b58>"

    do_redeem = do_claim


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = MobFriend()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
