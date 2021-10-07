import base64
import json
import time
import aiohttp
import mobilecoin
import forest_tables
import utils

mobilecoind: mobilecoin.Client = mobilecoin.Client("http://localhost:9090/wallet", ssl=False)  # type: ignore


def get_accounts() -> None:
    assert hasattr(mobilecoind, "get_all_accounts")
    raise NotImplementedError
    # account_id = list(mobilecoind.get_all_accounts().keys())[0]  # pylint: disable=no-member # type: ignore


async def mob(data: dict) -> dict:
    better_data = {"jsonrpc": "2.0", "id": 1, **data}
    async with aiohttp.ClientSession() as session:
        req = session.post(
            "http://full-service.internal/wallet",
            data=json.dumps(better_data),
            headers={"Content-Type": "application/json"},
        )
        async with req as resp:
            return await resp.json()


def b64_receipt_to_full_service_receipt(b64_string: str) -> dict:
    """Convert a b64-encoded protobuf Receipt into a full-service receipt object"""
    receipt_bytes = base64.b64decode(b64_string)
    receipt = mobilecoin.Receipt.FromString(receipt_bytes) # type: ignore

    full_service_receipt = {
        "object": "receiver_receipt",
        "public_key": receipt.public_key.SerializeToString().hex(),
        "confirmation": receipt.confirmation.SerializeToString().hex(),
        "tombstone_block": str(int(receipt.tombstone_block)),
        "amount": {
            "object": "amount",
            "commitment": receipt.amount.commitment.data.hex(),
            "masked_value": str(int(receipt.amount.masked_value)),
        },
    }
    return full_service_receipt


async def import_account() -> None:
    params = {
        "mnemonic": utils.get_secret("MNEMONIC"),
        "key_derivation_version": "2",
        "name": "falloopa",
        "next_subaddress_index": 2,
        "first_block_index": "3500",
    }
    await mob({"method": "import_account", "params": params})


async def get_address() -> str:
    res = await mob({"method": "get_all_accounts"})
    acc_id = res["result"]["account_ids"][0]
    return res["result"]["account_map"][acc_id]["main_address"]


async def get_receipt_amount(receipt_str: str) -> int:
    full_service_receipt = b64_receipt_to_full_service_receipt(receipt_str)
    params = {
        "address": utils.get_secret("address"),
        "receiver_receipt": full_service_receipt,
    }
    tx = await mob({"method": "check_receiver_receipt_status", "params": params})
    return tx["result"]["txo"]["value_pmob"]


def get_transactions() -> dict[str, dict[str, str]]:
    raise NotImplementedError
    # mobilecoin api changed, this needs to make full-service reqs
    # return mobilecoind.get_all_transaction_logs_for_account(account_id)  # type: ignore # pylint: disable=no-member


def local_main() -> None:
    last_transactions: dict[str, dict[str, str]] = {}
    payments_manager_connection = forest_tables.PaymentsManager()
    payments_manager_connection.sync_create_table()

    while True:
        latest_transactions = get_transactions()
        for transaction in latest_transactions:
            if transaction not in last_transactions:
                unobserved_tx = latest_transactions.get(transaction, {})
                short_tx = {}
                for k, v in unobserved_tx.items():
                    if isinstance(v, list) and len(v) == 1:
                        v = v[0]
                    if isinstance(v, str) and k != "value_pmob":
                        v = v[:16]
                    short_tx[k] = v
                print(short_tx)
                payments_manager_connection.sync_put_payment(
                    short_tx["transaction_log_id"],
                    short_tx["account_id"],
                    int(short_tx["value_pmob"]),
                    int(short_tx["finalized_block_index"]),
                )
        last_transactions = latest_transactions.copy()
        time.sleep(10)


if __name__ == "__main__":
    local_main()
