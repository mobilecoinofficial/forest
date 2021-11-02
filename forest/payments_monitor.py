import asyncio
import json
import logging
import time
from typing import Optional
import random
import asyncpg
import aiohttp

from forest import mc_util, utils
from forest.pghelp import Loop, PGExpressions, PGInterface

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
    put_pmob_tx="INSERT INTO {self.table} (account, amount_usd_cents, amount_pmob, memo, ts) \
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
        memo CHARACTER VARYING(32), \
        unique(unique_pmob))",
    create_invoice="INSERT INTO {self.table} (account, unique_pmob, memo) VALUES($1, $2, $3)",
    get_invoice_by_amount="SELECT invoice_id, account FROM {self.table} WHERE unique_pmob=$1",
)


class InvoiceManager(PGInterface):
    def __init__(self) -> None:
        super().__init__(InvoicePGEExpressions, DATABASE_URL, None)


class LedgerManager(PGInterface):
    def __init__(
        self,
        queries: PGExpressions = LedgerPGExpressions,
        database: str = DATABASE_URL,
        loop: Loop = None,
    ) -> None:
        super().__init__(queries, database, loop)


# def auxin_addr_to_b58(auxin_output: str) -> str:
# requires protobuf stuff
#     mc_util.b64_public_address_to_b58_wrapper(
#         base64.b64encode(
#             bytes(
#                 json.loads(auxin_output)
#                 .get("Address")
#                 .get("mobileCoinAddress")
#                 .get("address")
#             )
#         )
#     )


class Mobster:
    """Class to keep track of a aiohttp session and cached rate"""

    def __init__(self, url: str = "http://full-service.fly.dev/wallet") -> None:
        self.session = aiohttp.ClientSession()
        self.ledger_manager = LedgerManager()
        self.invoice_manager = InvoiceManager()
        self.url = url

    async def req_(self, method: str, **params: str) -> dict:
        logging.info("full-service request: %s", method)
        result = await self.req({"method": method, "params": params})
        if "error" in result:
            logging.info(result)
        return result

    async def req(self, data: dict) -> dict:
        better_data = {"jsonrpc": "2.0", "id": 1, **data}
        mob_req = self.session.post(
            self.url,
            data=json.dumps(better_data),
            headers={"Content-Type": "application/json"},
        )
        async with mob_req as resp:
            return await resp.json()

    rate_cache: tuple[int, Optional[float]] = (0, None)

    async def get_rate(self) -> float:
        """Get the current USD/MOB price and cache it for an hour"""
        hour = round(time.time() / 3600)  # same value within each hour
        if self.rate_cache[0] == hour and self.rate_cache[1] is not None:
            return self.rate_cache[1]
        try:
            url = "https://big.one/api/xn/v1/asset_pairs/8e900cb1-6331-4fe7-853c-d678ba136b2f"
            last_val = await self.session.get(url)
            resp_json = await last_val.json()
            mob_rate = float(resp_json.get("data").get("ticker").get("close"))
        except (
            aiohttp.ClientError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            logging.error(e)
            # big.one goes down sometimes, if it does... make up a price
            mob_rate = 14
        self.rate_cache = (hour, mob_rate)
        return mob_rate

    async def pmob2usd(self, pmob: int) -> float:
        return mc_util.pmob2mob(pmob) * await self.get_rate()

    async def usd2mob(self, usd: float, perturb: bool = False) -> float:
        invnano = 100000000
        # invpico = 100000000000 # doesn't work in mixin
        mob_rate = await self.get_rate()
        if perturb:
            # perturb each price slightly to have a unique payment
            mob_rate -= random.random() / 1000
        mob_amount = usd / mob_rate
        if perturb:
            return round(mob_amount, 8)
        return round(mob_amount, 3)  # maybe ceil?

    async def create_invoice(self, amount_usd: float, account: str, memo: str) -> float:
        while 1:
            try:
                mob_price_exact = await self.usd2mob(amount_usd, perturb=True)
                await self.invoice_manager.create_invoice(
                    account, mc_util.mob2pmob(mob_price_exact), memo
                )
                return mob_price_exact
            except asyncpg.UniqueViolationError:
                pass

    async def import_account(self) -> dict:
        params = {
            "mnemonic": utils.get_secret("MNEMONIC"),
            "key_derivation_version": "2",
            "name": "falloopa",
            "next_subaddress_index": "2",
            "first_block_index": "3500",
        }
        return await self.req({"method": "import_account", "params": params})

    # cache?
    async def get_address(self) -> str:
        res = await self.req({"method": "get_all_accounts"})
        acc_id = res["result"]["account_ids"][0]
        return res["result"]["account_map"][acc_id]["main_address"]

    async def get_receipt_amount_pmob(self, receipt_str: str) -> Optional[float]:
        full_service_receipt = mc_util.b64_receipt_to_full_service_receipt(receipt_str)
        logging.debug("fs receipt: %s", full_service_receipt)
        params = {
            "address": await self.get_address(),
            "receiver_receipt": full_service_receipt,
        }
        while 1:
            tx = await self.req(
                {"method": "check_receiver_receipt_status", "params": params}
            )
            logging.debug("receipt tx: %s", tx)
            # {'method': 'check_receiver_receipt_status', 'result':
            # {'receipt_transaction_status': 'TransactionPending', 'txo': None}, 'jsonrpc': '2.0', 'id': 1}
            if "error" in tx:
                return None
            if tx["result"]["receipt_transaction_status"] == "TransactionPending":
                await asyncio.sleep(1)
                continue
            pmob = int(tx["result"]["txo"]["value_pmob"])
            return pmob

    account_id: Optional[str] = None

    async def get_account(self) -> str:
        if not isinstance(self.account_id, str):
            self.account_id = (await self.req({"method": "get_all_accounts"}))[
                "result"
            ]["account_ids"][0]
        return self.account_id

    async def get_balance(self) -> str:
        return (
            await self.req(
                {
                    "method": "get_balance_for_account",
                    "params": {"account_id", await self.get_account()},
                }
            )
        )["result"]["balance"]["unspent_pmob"]

    async def get_transactions(self, account_id: str) -> dict[str, dict[str, str]]:
        return (
            await self.req(
                {
                    "method": "get_all_transaction_logs_for_account",
                    "params": {"account_id": account_id},
                }
            )
        )["result"]["transaction_log_map"]

    async def monitor_wallet(self) -> None:
        last_transactions: dict[str, dict[str, str]] = {}
        account_id = await self.get_account()
        while True:
            latest_transactions = await self.get_transactions(account_id)
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
                    value_pmob = int(short_tx["value_pmob"])
                    invoice = await self.invoice_manager.get_invoice_by_amount(
                        value_pmob
                    )
                    if invoice:
                        credit = await self.pmob2usd(value_pmob)
                        # (account, amount_usd_cent, amount_pmob, memo)
                        await self.ledger_manager.put_pmob_tx(
                            invoice[0].get("account"),
                            int(credit * 100),
                            value_pmob,
                            short_tx["transaction_log_id"],
                        )
                    # otherwise check if it's related to signal pay
                    # otherwise, complain about this unsolicited payment to an admin or something
            last_transactions = latest_transactions.copy()
            await asyncio.sleep(10)
