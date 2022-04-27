#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
import base64
import json
import logging
import random
import ssl
import time
from typing import Any, Optional
import aiohttp
import asyncpg

import mc_util
from forest import utils
from forest.pghelp import Loop, PGExpressions, PGInterface

if not utils.get_secret("ROOTCRT"):
    ssl_context: Optional[ssl.SSLContext] = None
else:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    root = open("rootcrt.pem", "wb")
    root.write(base64.b64decode(utils.get_secret("ROOTCRT")))
    root.flush()
    client = open("client.full.pem", "wb")
    client.write(base64.b64decode(utils.get_secret("CLIENTCRT")))
    client.flush()

    ssl_context.load_verify_locations("rootcrt.pem")
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.load_cert_chain(certfile="client.full.pem")

MICROMOB_TO_PICOMOB = 1_000_000  # that's 1e12/1e6
MILLIMOB_TO_PICOMOB = 1_000_000_000  # that's 1e12/1e3
FEE_PMOB = int(1e12 * 0.0004)  # mobilecoin transaction fee in picomob.

DATABASE_URL = utils.get_secret("DATABASE_URL")
LedgerPGExpressions = PGExpressions(
    table=utils.get_secret("LEDGER_NAME") or "ledger",
    create_table="""CREATE TABLE IF NOT EXISTS {self.table} ( 
        tx_id SERIAL PRIMARY KEY, 
        account TEXT,
        amount_usd_cents BIGINT NOT NULL, 
        amount_pmob BIGINT, 
        memo TEXT, 
        ts TIMESTAMP);""",
    put_usd_tx="INSERT INTO {self.table} (account, amount_usd_cents, memo, ts) \
        VALUES($1, $2, $3, CURRENT_TIMESTAMP);",
    put_pmob_tx="INSERT INTO {self.table} (account, amount_usd_cents, amount_pmob, memo, ts) \
        VALUES($1, $2, $3, $4, CURRENT_TIMESTAMP);",
    get_usd_balance="SELECT COALESCE(SUM(amount_usd_cents)/100, 0.0) AS balance \
        FROM {self.table} WHERE account=$1",
    get_pmob_balance="SELECT COALESCE(SUM(amount_pmob), 0.0) AS balance \
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


class Mobster:
    """Class to keep track of a aiohttp session and cached rate"""

    default_url = ()

    def __init__(self, url: str = "") -> None:
        if not url:
            url = (
                utils.get_secret("FULL_SERVICE_URL") or "http://localhost:9090/"
            ).removesuffix("/wallet") + "/wallet"

        self.account_id: Optional[str] = None
        logging.info("full-service url: %s", url)
        self.url = url

    async def req_(self, method: str, **params: Any) -> dict:
        logging.info("full-service request: %s", method)
        result = await self.req({"method": method, "params": params})
        if "error" in result:
            logging.error(result)
        return result

    async def req(self, data: dict) -> dict:
        better_data = {"jsonrpc": "2.0", "id": 1, **data}
        logging.debug("url is %s", self.url)
        async with aiohttp.TCPConnector(ssl=ssl_context) as conn:
            async with aiohttp.ClientSession(connector=conn) as sess:
                # this can hang (forever?) if there's no full-service at that url
                async with sess.post(
                    self.url,
                    data=json.dumps(better_data),
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    return await resp.json()

    async def get_all_txos_for_account(self) -> dict[str, dict]:
        txos = (
            (
                (
                    await self.req_(
                        "get_all_txos_for_account", account_id=await self.get_account()
                    )
                )
            )
            .get("result", {})
            .get("txo_map", {})
        )

        return txos

    async def get_utxos(self) -> dict[str, int]:
        txos = await self.get_all_txos_for_account()
        utxos = {
            txo: int(status.get("value_pmob", 0))
            for txo, status in txos.items()
            if status.get("account_status_map", {})
            .get(await self.get_account(), {})
            .get("txo_status")
            == "txo_status_unspent"
        }
        if utxos:
            sorted_ = dict(sorted(utxos.items(), key=lambda txo: utxos.get(txo[0], 0)))
            return sorted_
        return {}

    async def split_txos_slow(
        self, output_millimob: int = 100, target_quantity: int = 200
    ) -> str:
        output_pmob = output_millimob * MILLIMOB_TO_PICOMOB + FEE_PMOB
        built = 0
        i = 0
        utxos: list[tuple[str, int]] = list(reversed((await self.get_utxos()).items()))
        if sum([value for _, value in utxos]) < output_pmob * target_quantity:
            return "insufficient MOB"
        while built < (target_quantity + 3):
            if len(utxos) < 1:
                # if we have few big txos, we can have a lot of change we don't see yet
                # so if we've run out of txos we check for change from previous iterations
                utxos = list(reversed((await self.get_utxos()).items()))
            txo_id, value = utxos.pop(0)
            if value / output_pmob < 2:
                continue
            split_transaction = await self.req_(
                "build_split_txo_transaction",
                **dict(
                    txo_id=txo_id,
                    output_values=[
                        # if we can't split into 15, split into however much possible
                        str(output_pmob)
                        for _ in range(min(value // output_pmob, 15))
                    ],
                ),
            )
            params = split_transaction.get("result", {})
            # use this maybe
            if params:
                results = await self.req_("submit_transaction", **params)
            else:
                results = {}
            # not only did we have params, submitting also had a result
            if results.get("results"):
                await asyncio.sleep(2)
                built += 15
                i += 1
        await asyncio.sleep(10)
        return f"built {built} utxos each containing {output_millimob} mmob/ea"

    rate_cache: tuple[int, Optional[float]] = (0, None)

    async def get_rate(self) -> float:
        """Get the current USD/MOB price and cache it for an hour"""
        hour = round(time.time() / 3600)  # same value within each hour
        if self.rate_cache[0] == hour and self.rate_cache[1] is not None:
            return self.rate_cache[1]
        try:
            url = "https://big.one/api/xn/v1/asset_pairs/8e900cb1-6331-4fe7-853c-d678ba136b2f"
            last_val = await aiohttp.ClientSession().get(url)
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
        "takes picoMOB, returns USD"
        return float(mc_util.pmob2mob(pmob)) * await self.get_rate()

    async def usd2mob(self, usd: float, perturb: bool = False) -> float:
        "takes USD, returns MOB"
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

    async def import_account(self, name: str = "bot") -> dict:
        "import an account using the MNEMONIC secret"
        if not utils.get_secret("MNEMONIC"):
            raise ValueError
        params = {
            "mnemonic": utils.get_secret("MNEMONIC"),
            "key_derivation_version": "1",
            "name": name,
            "next_subaddress_index": "2",
            "first_block_index": "3500",
        }
        return await self.req({"method": "import_account", "params": params})

    async def ensure_address(self) -> str:
        """if we don't have an address, either import an account if MNEMONIC is set,
        or create a new account. then return our address"""
        try:
            await self.get_my_address()
        except IndexError:
            if utils.get_secret("MNEMONIC"):
                await self.import_account()
            else:
                await self.req_(method="create_account", name="bot")
        return await self.get_my_address()

    async def get_my_address(self) -> str:
        """Returns either the address set, or the address specified by the secret
        or the first address in the full service instance in that order"""
        acc_id = await self.get_account()
        res = await self.req({"method": "get_all_accounts"})
        return res["result"]["account_map"][acc_id]["main_address"]

    async def get_account(self, account_name: Optional[str] = None) -> str:
        """returns the account id matching account_name in Full Service Wallet"""

        if isinstance(self.account_id, str):
            return self.account_id

        if account_name is None:
            account_name = utils.get_secret("FS_ACCOUNT_NAME")

        ## get all account IDs for the Wallet / fullservice instance
        account_ids = (await self.req({"method": "get_all_accounts"}))["result"][
            "account_ids"
        ]
        maybe_account_id = []
        if account_name is not None:
            ## get the account map for the accounts in the wallet
            account_map = [
                (await self.req({"method": "get_all_accounts"}))["result"][
                    "account_map"
                ][x]
                for x in account_ids
            ]

            ## get the account_id that matches the name
            maybe_account_id = [
                x["account_id"] for x in account_map if x["name"] == account_name
            ]
        if len(maybe_account_id) == 0:
            account_id = account_ids[0]
            logging.info("falling back to zeroth account: %s", account_id)
        else:
            account_id = maybe_account_id[0]
        self.account_id = account_id
        return account_id

    async def get_receipt_amount_pmob(self, receipt_str: str) -> Optional[int]:
        full_service_receipt = mc_util.b64_receipt_to_full_service_receipt(receipt_str)
        logging.info("fs receipt: %s", full_service_receipt)
        params = {
            "address": await self.get_my_address(),
            "receiver_receipt": full_service_receipt,
        }
        while 1:
            tx = await self.req(
                {"method": "check_receiver_receipt_status", "params": params}
            )
            logging.info("receipt tx: %s", tx)
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

    async def get_balance(self) -> int:
        value = (
            await self.req(
                {
                    "method": "get_balance_for_account",
                    "params": {"account_id": await self.get_account()},
                }
            )
        )["result"]["balance"]["unspent_pmob"]
        return int(value)

    async def get_transactions(self, account_id: str) -> dict[str, dict[str, str]]:
        return (
            await self.req(
                {
                    "method": "get_all_transaction_logs_for_account",
                    "params": {"account_id": account_id},
                }
            )
        )["result"]["transaction_log_map"]

    async def build_single_txo_proposal(self, recipient: str, amount: str) -> dict:
        """
        Build proposal for single txo to single recipient.

        args:
          recipient (str): Base58 mobilecoin address of recipient
          amount (int): Amount in picomob to send to address

        returns:
          dict: Resulting proposal from mobilecoin
        """

        account_id = await self.get_account()
        tx_proposal = dict(
            method="build_transaction",
            params=dict(
                account_id=account_id,
                recipient_public_address=recipient,
                value_pmob=str(amount),
                log_tx_proposal=True,
            ),
        )

        return await self.req(tx_proposal)

    async def build_multi_txo_proposal(
        self, txo_proposals: list[tuple[str, str]]
    ) -> dict:
        """
        Submit a multiple txo transaction proposal to full-service api. Txos may
        be sent to a single address or multiple addresses.

        args:
          txo_proposals (list[tuple[str, int]]): List of (address, picomob) pairs

        Returns:
          dict: result of multi-output proposal
        """

        account_id = await self.get_account()
        tx_proposal = dict(
            method="build_transaction",
            params=dict(
                account_id=account_id,
                addresses_and_values=txo_proposals,
                log_tx_proposal=True,
            ),
        )

        return await self.req(tx_proposal)

    async def get_all_transaction_logs_by_block(self) -> dict:
        """
        Get all transactions for an account ordered by block

        Returns:
          dict: transaction records ordered by block
        """

        request = dict(method="get_all_transaction_logs_ordered_by_block")
        return await self.req(request)

    async def get_block(self, block: int) -> dict:
        """
        Get basic global statistics and statistics about specified block

        args:
          block (int): Mobilecoin block number

        Returns:
          dict: block information
        """

        request = dict(method="get_block", params=dict(block_index=str(block)))
        return await self.req(request)

    async def get_pending_transactions(self, from_block: int = 2) -> list[dict]:
        """
        Get pending transactions within account, optionally counting from a specific
        block

        args:
          from_block (int):

        Return:
          list[dict]: list of pending transactions
        """

        pending_transactions: list[dict] = []
        tx_logs = await self.get_all_transaction_logs_by_block()
        tx_logs = tx_logs.get("result", {}).get("transaction_log_map", {})
        for _, log in tx_logs.items():
            if log.get("status") == "tx_status_pending":
                try:
                    if int(log.get("submitted_block_index")) >= from_block:
                        pending_transactions.append(log)
                except (ValueError, TypeError):
                    continue

        return pending_transactions


class StatefulMobster(Mobster):
    def __init__(self) -> None:
        self.ledger_manager = LedgerManager()
        self.invoice_manager = InvoiceManager()
        super().__init__()

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
