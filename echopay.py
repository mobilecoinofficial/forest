#!/usr/bin/python3.9
import base64
import json
from typing import Optional
import logging

from aiohttp import web

import mc_util
from forest import utils
from forest.core import Bot, Message, Response, app

britbot = "+447888866969"
fee = int(1e12 * 0.0004)


class AuthorizedPayer(Bot):
    async def handle_message(self, message: Message) -> Response:
        if "hook me up" in message.text.lower():
            return await self.do_pay(message)
        return await super().handle_message(message)

    async def get_address(self, recipient: str) -> Optional[str]:
        result = await self.auxin_req("getPayAddress", peer_name=recipient)
        b64_address = (
            result.blob.get("Address", {}).get("mobileCoinAddress", {}).get("address")
        )
        if result.error or not b64_address:
            logging.info("bad address: %s", result.blob)
            return None
        address = mc_util.b64_public_address_to_b58_wrapper(b64_address)
        return address

    async def do_address(self, msg: Message) -> Response:
        address = await self.get_address(msg.source)
        return address or "sorry, couldn't get your MobileCoin address"

    async def send_payment(self, recipient: str, amount_pmob: int) -> Optional[Message]:
        logging.info("getting pay address")
        address = await self.get_address(recipient)
        if not address:
            await self.send_message(
                recipient, "sorry, couldn't get your MobileCoin address"
            )
            return None
        # TODO: add a lock around two-part build/submit OR
        # TODO: add explicit utxo handling
        # TODO: add task which keeps full-service filled
        raw_prop = await self.mobster.req_(
            "build_transaction",
            account_id=await self.mobster.get_account(),
            recipient_public_address=address,
            value_pmob=str(int(amount_pmob)),
            fee=str(fee),
        )
        prop = raw_prop["result"]["tx_proposal"]
        await self.mobster.req_("submit_transaction", tx_proposal=prop)
        receipt_resp = await self.mobster.req_(
            "create_receiver_receipts",
            tx_proposal=prop,
            account_id=await self.mobster.get_account(),
        )
        receipt = receipt_resp["result"]["receiver_receipts"][0]
        u8_receipt = [
            int(char)
            for char in base64.b64decode(
                mc_util.full_service_receipt_to_b64_receipt(receipt)
            )
        ]
        resp = await self.auxin_req(
            "send", simulate=True, message="", destination=recipient
        )
        content_skeletor = json.loads(resp.blob["simulate_output"])
        content_skeletor["dataMessage"]["body"] = None
        content_skeletor["dataMessage"]["payment"] = {
            "Item": {
                "notification": {
                    "note": "check out this java-free payment notification",
                    "Transaction": {"mobileCoin": {"receipt": u8_receipt}},
                }
            }
        }
        payment_notif = await self.auxin_req(
            "send", destination=recipient, content=json.dumps(content_skeletor)
        )
        await self.send_message(recipient, "receipt sent!")
        return payment_notif

    async def do_pay(self, msg: Message) -> Response:
        payment_notif_sent = await self.send_payment(msg.source, int(1e9))
        if payment_notif_sent:
            logging.info(payment_notif_sent)
            delta = (payment_notif_sent.timestamp - msg.timestamp) / 1000
            await self.send_message(
                utils.get_secret("ADMIN"), f"payment delta: {delta}"
            )
            self.auxin_roundtrip_latency.append(
                (msg.timestamp, "payment", delta)
            )
        return None

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        payment_notif = await self.send_payment(msg.source, amount_pmob - fee)
        if not payment_notif:
            return None
        delta = (payment_notif.timestamp - msg.timestamp) / 1000
        self.auxin_roundtrip_latency.append(
            (msg.timestamp, "repayment", delta)
        )
        await self.send_message(utils.get_secret("ADMIN"), f"repayment delta: {delta}")
        return None


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = AuthorizedPayer()

    web.run_app(app, port=8080, host="0.0.0.0")
