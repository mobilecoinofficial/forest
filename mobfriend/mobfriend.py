#!/usr/bin/python3.9
import asyncio
import glob
import logging
from decimal import Decimal
from typing import Any, Dict

import aioprocessing
import base58
from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary

from amzqr import amzqr
from scan import scan

import mc_util
from forest.core import Message, PayBot, Response, app, hide
from mc_util import mob2pmob, pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class MobFriend(PayBot):
    no_repay: list[str] = []
    exchanging_gift_code: list[str] = []
    user_images: Dict[str, str] = {}

    async def handle_message(self, message: Message) -> Response:
        if message.attachments and len(message.attachments):
            await asyncio.sleep(2)
            attachment_info = message.attachments[0]
            attachment_path = attachment_info.get("fileName")
            timestamp = attachment_info.get("uploadTimestamp")
            if attachment_path is None:
                attachment_paths = glob.glob(f"/tmp/unnamed_attachment_{timestamp}.*")
                if len(attachment_paths) > 0:
                    attachment_path = attachment_paths.pop()
                    self.user_images[message.source] = f"{attachment_path}"
            else:
                self.user_images[message.source] = f"/tmp/{attachment_path}"
            contents = scan(self.user_images[message.source])
            if contents:
                return contents[-1][1].decode()
            if not message.command:
                return f"OK, saving this image as {attachment_path} for later!"
        return await super().handle_message(message)

    async def _actually_build_wait_and_send_qr(
        self, text: str, user_id: str, image_path: Any = None
    ) -> str:
        if not image_path:
            image_path = self.user_images.get(user_id, "template.png")
        if image_path and "." in image_path:
            extension = image_path.split(".")[-1]
        else:
            extension = "png"
        save_name = f"{user_id}_{base58.b58encode(text[:16]).decode()}.{extension}"
        default_params: dict[str, Any] = dict(save_name=save_name, save_dir="/tmp")
        if image_path:
            default_params.update(
                dict(
                    version=1,
                    level="H",
                    colorized=False,
                    contrast=1.0,
                    brightness=1.0,
                    picture=image_path,
                )
            )
        await self.send_message(user_id, "Building your QR code! Please be patient!")
        p = aioprocessing.AioProcess(
            target=amzqr.run, args=(text,), kwargs=default_params
        )
        p.start()  # pylint: disable=no-member
        await p.coro_join()  # pylint: disable=no-member
        await self.send_message(user_id, text, attachments=[f"/tmp/{save_name}"])
        return save_name

    async def do_clear(self, msg: Message) -> Response:
        """Clears (if relevant) any saved images."""
        if msg.source in self.user_images:
            return f"Will use default instead of {self.user_images.pop(msg.source)} for next QR code."
        return "No images saved."

    async def do_makeqr(self, msg: Message) -> None:
        """
        /makeqr [text]
          or
        /makeqr "Longer Bit Of Text"

        I'll make a QR Code from the provided text!
        If you send me an image first, I'll use it as a template.
        """

        await self._actually_build_wait_and_send_qr(str(msg.arg1), msg.source)
        return None

    @hide
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
            resp_list = await self.build_gift_code(amount_pmob - FEE)
            gift_code = resp_list[1]
            self.exchanging_gift_code.remove(msg.source)
            if msg.source in self.no_repay:
                self.no_repay.remove(msg.source)
            await self._actually_build_wait_and_send_qr(gift_code, msg.source)
            return None
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

    async def do_show_details(self, msg: Message) -> Response:
        """
        /show_details [base58 code]
        Returns detailed information about a base58 code."""
        if msg.arg1:
            details = mc_util.b58_wrapper_to_protobuf(msg.arg1)
            if details:
                return str(details)
            return "Sorry, the provided code has an invalid checksum."
        return "Please provide a base58 code!"

    async def do_payme(self, msg: Message) -> Response:
        """
        /payme [amount] [memo]

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
        address_proto = mc_util.b58_wrapper_to_protobuf(address)
        if address_proto:
            payload.payment_request.public_address.CopyFrom(
                address_proto.public_address
            )
        else:
            return (
                "Sorry, could not parse a valid MobileCoin address from your profile!"
            )
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
        await self._actually_build_wait_and_send_qr(payment_request_b58, msg.source)
        return None

    async def do_paywallet(self, msg: Message) -> Response:
        """
        /paywallet [b58address] [amount] [memo]

        Creates a payment request (as QR code and b58 code to copy and paste.)
        For example, /paywallet [address] 1.0 "Pay me a MOB!"
        will create a payment request with
            * the destination [b58address],
            * a 1MOB value,
            * the memo "Pay me a MOB!"
        """
        address = msg.arg1
        amount = msg.arg2
        memo = msg.arg3 or ""
        if not address:
            return "Please provide your b58 address as the first argument!"
        payload = mc_util.printable_pb2.PrintableWrapper()
        address_proto = mc_util.b58_wrapper_to_protobuf(address)
        if not address_proto:
            return "Sorry, could not find a valid address!"
        payload.payment_request.public_address.CopyFrom(address_proto.public_address)
        if not amount or not amount.replace(".", "0", 1).isnumeric():
            return "Sorry, you need to provide a price (in MOB)!"
        payload.payment_request.value = mob2pmob(Decimal(amount))
        payload.payment_request.memo = memo
        payment_request_b58 = mc_util.add_checksum_and_b58(payload.SerializeToString())
        await self._actually_build_wait_and_send_qr(payment_request_b58, msg.source)
        return None

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
