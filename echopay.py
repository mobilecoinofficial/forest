#!/usr/bin/python3.9
import asyncio
import base64
import json
import logging
import sys
import traceback
from typing import Any

import termcolor
from aiohttp import web

import mc_util
from forest.core import Bot, Message, Response, app, rpc
from forest.message import AuxinMessage

britbot = "+447888866969"
fee = int(1e12 * 0.0004)


class AuthorizedPayer(Bot):
    pending_requests: dict[Any, asyncio.Future[Message]] = {}

    async def handle_auxincli_raw_line(self, line: str) -> None:
        if '{"jsonrpc":"2.0","result":[],"id":"receive"}' not in line:
            pass  # logging.error("auxin: %s", line)
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            logging.info("auxin: %s", line)
            return
        if "error" in blob:
            logging.info("auxin: %s", line)
            logging.error(termcolor.colored(blob["error"], "red"))
        try:
            if "result" in blob:
                if isinstance(blob.get("result"), list):
                    for msg in blob.get("result"):
                        await self.auxincli_output_queue.put(AuxinMessage(msg))
                    return
                msg = AuxinMessage(blob)
                await self.auxincli_output_queue.put(msg)
        except KeyError as e:  # ?
            logging.info("auxin parse error: %s", line)
            traceback.print_exception(*sys.exc_info())
            return
        # if msg.full_text:
        #   logging.info("signal: %s", line)
        return

    async def start_process(self) -> None:
        asyncio.create_task(self.recv_loop())
        await super().start_process()

    async def recv_loop(self) -> None:
        while not self.exiting:
            await self.auxincli_input_queue.put(rpc("receive", id="receive"))
            await asyncio.sleep(1)

    # async def wait_resp(self, cmd: dict) -> AuxinMessage:
    #     stamp = str(round(time.time()))
    #     cmd["id"] = stamp
    #     self.pending_requests[stamp] = asyncio.Future()
    #     await self.auxincli_input_queue.put(cmd)
    #     result = await self.pending_requests[stamp]
    #     self.pending_requests.pop(stamp)
    #     return result

    async def auxin_req(self, method: str, **params: Any) -> Message:
        return await self.wait_resp(rpc(method, **params))

    async def handle_message(self, msg: Message) -> Response:
        if "hit me up" in msg.text.lower():
            return await self.do_pay(msg)
        return await super().handle_message(msg)

    async def send_payment(self, recipient: str, amount_pmob: int) -> None:
        logging.info("getting pay address")
        result = await self.auxin_req("getPayAddress", peer_name=recipient)
        b64_address = (
            result.blob.get("Address", {}).get("mobileCoinAddress", {}).get("address")
        )
        if result.error or not b64_address:
            logging.info("bad address: %s", result.blob)
            await self.send_message("sorry, couldn't get your MobileCoin address")
        logging.info("got pay address")
        address = mc_util.b64_public_address_to_b58_wrapper(b64_address)
        await self.send_message(recipient, "got your address")
        raw_prop = await self.mobster.req_(
            "build_transaction",
            account_id=await self.mobster.get_account(),
            recipient_public_address=address,
            value_pmob=str(int(amount_pmob)),
            fee=str(fee),
        )
        prop = raw_prop["result"]["tx_proposal"]
        await self.mobster.req_("submit_transaction", tx_proposal=prop)
        await self.send_message(recipient, "payment sent")
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
        await self.auxin_req(
            "send", destination=recipient, content=json.dumps(content_skeletor)
        )
        await self.send_message(recipient, "receipt sent!")

    async def do_pay(self, msg: AuxinMessage) -> Response:
        # 1e9=1 milimob (.01 usd today)
        asyncio.create_task(self.send_payment(msg.source, int(1e9)))
        return "trying to send a payment"

    async def payment_response(self, msg: Message, amount: int) -> Response:
        asyncio.create_task(self.send_payment(msg.source, amount - fee))
        return f"trying to send you back {mc_util.pmob2mob(amount - fee)} MOB"


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = AuthorizedPayer()

    web.run_app(app, port=8080, host="0.0.0.0")
