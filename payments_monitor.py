import time
import mobilecoin
import forest_tables
import base64


mobilecoind: mobilecoin.Client = mobilecoin.Client("http://localhost:9090/wallet", ssl=False)  # type: ignore


def get_accounts() -> None:
    assert hasattr(mobilecoind, "get_all_accounts")
    raise NotImplementedError
    # account_id = list(mobilecoind.get_all_accounts().keys())[0]  # pylint: disable=no-member # type: ignore




def b64_receipt_to_full_service_receipt(b64_string):
    """Convert a b64-encoded protobuf Receipt into a full-service receipt object"""
    receipt_bytes = base64.b64decode(b64_string)
    receipt = mobilecoin.Receipt.ParseFromString(receipt_bytes)

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
