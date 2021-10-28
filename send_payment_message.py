import asyncio
import base64
import mobilecoin
import payments_monitor

num = os.listdir("data")[1]
mob = payments_monitor.Mobster()
britbot = "+447888866969"


async def get_output(cmd: str) -> str:
    getaddr = await asyncio.create_subprocess_shell(cmd, stdout=-1)
    stdout, _ = await getaddr.communicate()
    return stdout.decode().split("\n")[1]


attr = await get_output("auxin-cli -c './data' -u {num} getpayaddress {britbot}")
britbot_addr = mc_util.b64_public_address_to_b58_wrapper(
    base64.b64encode(
        bytes(json.loads(addr).get("Address").get("mobileCoinAddress").get("address"))
    )
)
raw_prop = await mob.req(
    {
        "method": "build_transaction",
        "params": {
            "account_id": acc,
            "recipient_public_address": britbot_addr,
            "value_pmob": amnt,
            "fee": fee,
            "comment": "test2",
        },
    }
)
prop = raw_prop["result"]["tx_proposal"]
await mob.req_("submit_transaction", tx_proposal=prop)
receipt = (
    await mob.req_("create_receiver_receipts", tx_proposal=prop, account_id=acc)
)["result"]["receiver_receipts"][0]
u8_receipt = [
    int(str(char))
    for char in base64.b64decode(mc_util.full_service_receipt_to_b64_receipt(receipt))
]
content_skeletor = json.loads(
    await get_output(f"auxin-cli --config data --user {num} send -s -m foo {britbot}")
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

content = json.dumps(content_skeletor)
get_output(f"auxin-cli -c data -u {num} send -c '{content}' {britbot}")
