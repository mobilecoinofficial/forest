#!/usr/bin/python3.9
import logging
import mc_util

import pyqrcode

from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary

from forest.core import PayBot, Message, Response, app
from decimal import Decimal

FEE = int(1e12 * 0.0004)

REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class PayFriend(PayBot):
    no_repay: list[str] = []
    exchanging_cash_code: list[str] = []

    async def handle_message(self, message: Message) -> Response:
        if "hook me up" in message.text.lower():
            return await self.do_pay(message)
        return await super().handle_message(message)

    async def do_make_cash_code(self, msg: Message) -> Response:
        if msg.source in self.no_repay:
            self.exchanging_cash_code.remove(msg.source)
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that cash code."
        self.no_repay.append(msg.source)
        self.exchanging_cash_code.append(msg.source)
        return "Your next transaction will be converted into a MobileCoin Cash Code that can be redeemed in other wallets."

    @time(REQUEST_TIME)  # type: ignore
    async def do_pay(self, msg: Message) -> Response:
        if msg.arg1:
            payment_notif_sent = await self.send_payment(
                msg.source, mc_util.mob2pmob(float(msg.arg1))
            )
        else:
            payment_notif_sent = await self.send_payment(
                msg.source, mc_util.mob2pmob(0.001)
            )
        if payment_notif_sent:
            logging.info(payment_notif_sent)
            delta = (payment_notif_sent.timestamp - msg.timestamp) / 1000
            await self.admin(f"payment delta: {delta}")
            self.auxin_roundtrip_latency.append((msg.timestamp, "payment", delta))
        return None

    @time(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        if msg.source not in self.no_repay:
            payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
            if not payment_notif:
                return None
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            await self.admin(f"repayment delta: {delta}")
            return None
        elif msg.source in self.exchanging_cash_code:
            resp = await self.build_cash_code(msg.source, amount_pmob)
            self.exchanging_cash_code.pop(msg.source)
            self.no_repay.pop(msg.source)
            return resp
        else:
            return f"Received {mc_util.pmob2mob(amount_pmob)}MOB"

    async def do_eval(self, msg: Message) -> Response:
        import ast

        async def async_exec(stmts, env=None):
            parsed_stmts = ast.parse(stmts)
            fn_name = "_async_exec_f"
            fn = f"async def {fn_name}(): pass"
            parsed_fn = ast.parse(fn)
            for node in parsed_stmts.body:
                ast.increment_lineno(node)
            parsed_fn.body[0].body = parsed_stmts.body
            exec(compile(parsed_fn, filename="<ast>", mode="exec"), env)
            return await eval(f"{fn_name}()", env)

        return str(await async_exec(" ".join(msg.tokens), locals()))

    async def do_balance(self, msg: Message) -> Response:
        return str(await self.mobster.get_balance())

    async def do_check_balance(self, msg: Message) -> Response:
        if msg.arg1:
            status = await self.mobster.req_(
                "check_gift_code_status", gift_code_b58=msg.arg1
            )
            pmob = status.get("result", {}).get("gift_code_value")
            if pmob:
                mob_amt = mc_util.pmob2mob(pmob)
                return f"Found a giftcard redeemable for {(mob_amt-Decimal(0.0004)).quantize(Decimal('1.0000'))}MOB."
            else:
                return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"
        else:
            return "/check_balance <b58>"

    async def do_check_b58_type(self, msg: Message) -> Response:
        if not msg.arg1:
            return "/check_b58_type <b58>"
        status = await self.mobster.req_("check_b58_type", b58_code=msg.arg1)
        if status.get("result", {}).get("b58_type") == "PaymentRequest":
            status["result"]["data"]["type"] = "PaymentRequest"
            status["result"]["data"]["value"] = str(
                mc_util.pmob2mob(status["result"]["data"]["value"])
            )
            return status.get("result").get("data")
        elif status.get("result", {}).get("b58_type") == "TransferPayload":
            return await self.do_check_balance(msg)
        else:
            return status.get("result")

    do_check58 = do_check_b58_type
    do_check = do_check_b58_type

    async def do_create_payment_request(self, msg: Message) -> Response:
        address = await self.get_address(msg.source)
        if not address:
            return "Unable to retrieve your MobileCoin address!"
        payload = mc_util.printable_pb2.PrintableWrapper()
        payload.payment_request.public_address.CopyFrom(
            mc_util.b58_wrapper_to_transfer_payload(address).public_address
        )
        if not (len(msg.tokens) > 0 and msg.tokens[0].replace(".", "0", 1).isnumeric()):
            return "Sorry, you need to provide a price (in MOB)!"
        payload.payment_request.value = mc_util.mob2pmob(float(msg.tokens[0]))
        if len(msg.tokens) > 1:
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

    async def do_qr(self, msg: Message) -> Response:
        if len(msg.tokens):
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

    do_payme = do_create_payment_request

    async def do_fsr(self, msg: Message) -> Response:
        msg.tokens = [
            token if not token.isnumeric() else int(token) for token in msg.tokens
        ]
        if len(msg.tokens) == 1:
            return await self.mobster.req(dict(method=msg.tokens[0]))
        if len(msg.tokens) == 3:
            return str(
                await self.mobster.req_(msg.tokens[0], **{msg.tokens[1]: msg.tokens[2]})
            )
        if len(msg.tokens) == 5:
            return str(
                await self.mobster.req_(
                    msg.tokens[0],
                    **{msg.tokens[1]: msg.tokens[2], msg.tokens[3]: msg.tokens[4]},
                )
            )
        else:
            return "/fsr <command> (<arg1> <val1>( <arg2> <val2>))"

    async def do_claim_balance(self, msg: Message) -> Response:
        if msg.arg1:
            status = await self.mobster.req_(
                "check_gift_code_status", gift_code_b58=msg.arg1
            )
            amount_pmob = status.get("result", {}).get("gift_code_value")
            status = await self.mobster.req_(
                "claim_gift_code",
                gift_code_b58=msg.arg1,
                account_id=await self.mobster.get_account(),
            )
            # pmob = status.get("result", {}).get("gift_code_value")
            if amount_pmob:
                payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
                amount_mob = mc_util.pmob2mob(amount_pmob)
                return (
                    f"Claimed a giftcard containing {str(float(amount_mob)-0.0004).rstrip('0')}MOB."
                    + "\n"
                    + str(status)
                )
            else:
                return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"
        else:
            return "/check_balance <b58>"


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = PayFriend()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
