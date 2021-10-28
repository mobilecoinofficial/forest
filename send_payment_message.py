import asyncio
import base64
import json
import time
from typing import Any
from collections import defaultdict

import mc_util  # actually needs printable_pb2
import mobilecoin
import payments_monitor

from forest.core import Bot, Message, Response

britbot = "+447888866969"


async def get_output(cmd: str) -> str:
    getaddr = await asyncio.create_subprocess_shell(cmd, stdout=-1)
    stdout, _ = await getaddr.communicate()
    return stdout.decode().split("\n")[1]


class AuthorizedPayer(Bot):
    # idea from scala's cats-effect MVar
    pending_requests: dict[str, asyncio.Queue[Message]] = defaultdict(
        lambda: asyncio.Queue(maxsize=1)
    )

    async def handle_message(self, msg: Message) -> Response:
        # if it's not a generic receive message or such, put it in a queue
        if msg.id not in (0, 1):
            await self.pending_requests[msg.id].put(msg)
            return None
        return await super().handle_message(msg)

    async def wait_resp(self, cmd: dict) -> dict:
        stamp = str(round(time.time()))
        cmd["id"] = stamp
        self.signalcli_input_queue.put(cmd)
        # when the response is received, it'll be in a queue under this key
        return await self.pending_requests[stamp].get()

    async def auxin_req(self, method: str, **params: Any) -> dict:
        q = {"jsonrpc": "2.0", "method": method, "params": params}
        return (await self.wait_resp(q))["result"]

    async def send_payment(self, recipient: str, amount_pmob: int) -> None:
        result = await self.auxin_req("getPayAddress", peer_name=recipient)
        address = mc_util.b64_public_address_to_b58_wrapper(
            base64.b64encode(
                bytes(
                    result.get("Address", {})
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
