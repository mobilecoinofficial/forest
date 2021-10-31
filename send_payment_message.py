#!/usr/bin/python3.9
import logging
import termcolor
import asyncio
import base64
import json
import time
from typing import Any
from aiohttp import web

import mc_util  # actually needs printable_pb2
import mobilecoin
from forest import payments_monitor

from forest.message import AuxinMessage
from forest.core import Bot, Message, Response, app, rpc

britbot = "+447888866969"



class AuthorizedPayer(Bot):
    pending_requests: dict[Any, asyncio.Future[Message]] = {}

    async def handle_auxincli_raw_line(self, line: str) -> None:
        logging.info("auxin: %s", line)
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            logging.info("auxin: %s", line)
            return
        if "error" in blob:
            logging.error(termcolor.colored(blob["error"], "red"))
        try:
            if "result" in blob:
                if isinstance(blob.get("result"), list):
                    for msg in blob.get("result"):
                        logging.info("received message")
                        await self.auxincli_output_queue.put(AuxinMessage(msg))
                    return
                msg = AuxinMessage(blob)
        except KeyError:  # ?
            logging.info("auxin parse error: %s", line)
            return
        #if msg.full_text:
        #   logging.info("signal: %s", line)
        await self.auxincli_output_queue.put(msg)
        return

    async def start_process(self) -> None:
        asyncio.create_task(self.recv_loop())
        await super().start_process()

    async def recv_loop(self) -> None:
        while not self.exiting:
            await self.auxincli_input_queue.put(rpc("receive", id="receive"))
            await asyncio.sleep(1)

    async def wait_resp(self, cmd: dict) -> AuxinMessage:
        stamp = str(round(time.time()))
        cmd["id"] = stamp
        self.pending_requests[stamp] = asyncio.Future()
        self.auxincli_input_queue.put(cmd)
        result = await self.pending_requests[stamp]
        self.pending_requests.pop(stamp)
        return result

    async def auxin_req(self, method: str, **params: Any) -> AuxinMessage:
        return (await self.wait_resp(rpc(method, **params)))

    async def send_payment(self, recipient: str, amount_pmob: int) -> None:
        result = await self.auxin_req("getPayAddress", peer_name=recipient)
        address = mc_util.b64_public_address_to_b58_wrapper(
            base64.b64encode(
                bytes(
                    result.blob.get("Address", {})
                    .get("mobileCoinAddress", {})
                    .get("address")
                )
            )
        )
        raw_prop = await self.mobster.req_(
            "build_transaction",
            account_id=await self.mobster.get_account(),
            recipient_public_address=address,
            value_pmob=str(amount_pmob),
            fee="400000000",
        )
        prop = raw_prop["result"]["tx_proposal"]
        await self.mobster.req_("submit_transaction", tx_proposal=prop)
        receipt_resp = await self.mobster.req_(
            "create_receiver_receipts",
            tx_proposal=prop,
            account_id=self.mobster.account_id,
        )
        receipt = receipt_resp["result"]["receiver_receipts"][0]
        u8_receipt = [
            int(char)
            for char in base64.b64decode(
                mc_util.full_service_receipt_to_b64_receipt(receipt)
            )
        ]
        content_skeletor = json.loads(
            (await self.auxin_req("send", simulate=True, message=""))["simulate_output"]
        )
        content_skeletor["dataMessage"]["body"] = None
        content_skeletor["dataMessage"]["payment"] = {
            "Item": {
                "notification": {
                    "note": "foo",
                    "Transaction": {"mobileCoin": {"receipt": u8_receipt}},
                }
            }
        }
        await self.auxin_req(
            "send", destination=recipient, content=json.dumps(content_skeletor)
        )

    async def do_pay(self, msg: AuxinMessage) -> str:
        # 1e9=1 milimob (.01 usd today)
        asyncio.create_task(self.send_payment(msg.source, 1e9))
        return "trying to send a payment"


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = AuthorizedPayer()

    web.run_app(app, port=8080, host="0.0.0.0")
