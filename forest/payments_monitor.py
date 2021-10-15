from typing import Optional
import json
import logging
import aiohttp
from forest import utils
from forest import mc_util
from forest.pghelp import PGExpressions, PGInterface, Loop

DATABASE_URL = utils.get_secret("DATABASE_URL")
LedgerPGExpressions = PGExpressions(
    table="ledger",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} ( \
        tx_id SERIAL PRIMARY KEY, \
        account CHARACTER VARYING(16), \
        amount_usd_cents BIGINT NOT NULL, \
        amount_pmob BIGINT, \
        memo CHARACTER VARYING(32), \
        invoice CHARACTER VARYING(32), \
        ts TIMESTAMP);",
    put_usd_tx="INSERT INTO {self.table} (account, amount_usd_cents, memo, ts) \
        VALUES($1, $2, $3, CURRENT_TIMESTAMP);",
    put_pmob_tx="INSERT INTO {self.table} (account, amount_usd_cent, amount_pmob, memo, ts) \
        VALUES($1, $2, $3, $4, CURRENT_TIMESTAMP);",
    get_usd_balance="SELECT COALESCE(SUM(amount_usd_cents)/100, 0.0) AS balance \
        FROM {self.table} WHERE account=$1",
)

InvoicePGEExpressions = PGExpressions(
    table="invoices",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} (\
        invoice_id SERIAL PRIMARY KEY, \
        account CHARACTER VARYING(16), \
        unique_pmob BIGINT, \
        memo CHARECTER VARYING(32) \
        unique(unique_pmob)",
    create_invoice="INSERT INTO {self.table} (account, unique_pmob, memo) VALUES($1, $2, $3)"
    get_invoice_by_amount="SELECT invoice_id, account FROM {self.table} WHERE unique_pmob=$1"
)
class InvoiceManager(PGInterface):
    def __init__(self):
        super().__init__(InvoicePGEExpressions, DATABASE_URL, None)


class LedgerManager(PGInterface):
    def __init__(
        self,
        queries: PGExpressions = LedgerPGExpressions,
        database: str = DATABASE_URL,
        loop: Loop = None,
    ) -> None:
        super().__init__(queries, database, loop)


async def mob(data: dict) -> dict:
    better_data = {"jsonrpc": "2.0", "id": 1, **data}
    async with aiohttp.ClientSession() as session:
        req = session.post(
            "http://full-service.fly.dev/wallet",
            data=json.dumps(better_data),
            headers={"Content-Type": "application/json"},
        )
        async with req as resp:
            return await resp.json()


async def import_account() -> dict:
    params = {
        "mnemonic": utils.get_secret("MNEMONIC"),
        "key_derivation_version": "2",
        "name": "falloopa",
        "next_subaddress_index": "2",
        "first_block_index": "3500",
    }
    return await mob({"method": "import_account", "params": params})


# cache?
async def get_address() -> str:
    res = await mob({"method": "get_all_accounts"})
    acc_id = res["result"]["account_ids"][0]
    return res["result"]["account_map"][acc_id]["main_address"]


async def get_receipt_amount_pmob(receipt_str: str) -> Optional[float]:
    full_service_receipt = mc_util.b64_receipt_to_full_service_receipt(receipt_str)
    logging.debug(full_service_receipt)
    params = {
        "address": await get_address(),
        "receiver_receipt": full_service_receipt,
    }
    tx = await mob({"method": "check_receiver_receipt_status", "params": params})
    logging.debug(tx)
    if "error" in tx:
        return None
    pmob = int(tx["result"]["txo"]["value_pmob"])
    return pmob


def get_account() -> str:
    account_id = await mob({"method": "get_all_accounts"})["result"]["account_ids"][0]


def get_transactions(account_id: str) -> dict[str, dict[str, str]]:
    return (
        await mob(
            {
                "method": "get_all_transaction_logs_for_account",
                "params": {"account_id": account_id},
            }
        )
    )["result"]["transaction_log_map"]


def local_main() -> None:
    last_transactions: dict[str, dict[str, str]] = {}
    payments_manager_connection = PaymentsManager()
    payments_manager_connection.sync_create_table()
    invoice_manager = InvoiceManager()
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
                logging.info(short_tx)
                invoice = await invoice_manager.get_invoice_by_amount(value_pmob)
                if invoice:

                    credit = await pmob_to_usd(value_pmob)
                   await transaction_manager.put_transaction(invoice.user, credit)
                # otherwise check if it's related to signal pay
                # otherwise, complain about this unsolicited payment to an admin or something
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
