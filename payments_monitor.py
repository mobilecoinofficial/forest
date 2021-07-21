import time
import mobilecoin
import forest_tables


mobilecoind = mobilecoin.Client()
account_id = list(mobilecoind.get_all_accounts().keys())[0]


def get_transactions():
    return mobilecoind.get_all_transaction_logs_for_account(account_id)


def local_main():
    last_transactions = {}
    payments_manager_connection = forest_tables.PaymentsManager()
    payments_manager_connection.sync_create_table()

    while True:
        latest_transactions = get_transactions()
        for transaction in latest_transactions:
            if transaction not in last_transactions:
                unobserved_tx = latest_transactions.get(transaction)
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
