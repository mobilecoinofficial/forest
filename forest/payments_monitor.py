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
from typing import Optional, Union, Any

import aiohttp
import asyncpg

import mc_util
from forest import utils
from forest.pghelp import Loop, PGExpressions, PGInterface

ROOTCRT, CLIENTCRT, FULL_SERVICE_URL = "ROOTCRT", "CLIENTCRT", "FULL_SERVICE_URL"
if utils.get_secret("USE_TESTNET"):
    ROOTCRT = "TESTNET_" + ROOTCRT
    CLIENTCRT = "TESTNET_" + CLIENTCRT
    FULL_SERVICE_URL = "TESTNET_" + FULL_SERVICE_URL

if not utils.get_secret("ROOTCRT"):
    ssl_context: Optional[ssl.SSLContext] = None
else:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    root = open("rootcrt.pem", "wb")
    root.write(base64.b64decode(utils.get_secret(ROOTCRT)))
    root.flush()
    client = open("client.full.pem", "wb")
    client.write(base64.b64decode(utils.get_secret(CLIENTCRT)))
    client.flush()

    ssl_context.load_verify_locations("rootcrt.pem")
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.load_cert_chain(certfile="client.full.pem")


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

    default_url = ()

    def __init__(self, url: str = "") -> None:
        if not url:
            url = (
                utils.get_secret(FULL_SERVICE_URL)
                or "http://full-service.fly.dev/wallet"
            )
        self.ledger_manager = LedgerManager()
        self.invoice_manager = InvoiceManager()
        logging.info("full-service url: %s", url)
        self.url = url

    async def req_(self, method: str, **params: str) -> dict:
        logging.info("full-service request: %s", method)
        result = await self.req({"method": method, "params": params})
        if "error" in result:
            logging.error(result)
        return result

    async def req(self, data: dict) -> dict:
        better_data = {"jsonrpc": "2.0", "id": 1, **data}
        logging.debug("url is %s", self.url)
        conn = aiohttp.TCPConnector(ssl=ssl_context)
        mob_req = aiohttp.ClientSession(connector=conn).post(
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
        return float(mc_util.pmob2mob(pmob)) * await self.get_rate()

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
            "key_derivation_version": "1",
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

    async def get_receipt_amount_pmob(self, receipt_str: str) -> Optional[int]:
        full_service_receipt = mc_util.b64_receipt_to_full_service_receipt(receipt_str)
        logging.info("fs receipt: %s", full_service_receipt)
        params = {
            "address": await self.get_address(),
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

    async def get_account(self) -> str:
        if not isinstance(self.account_id, str):
            self.account_id = (await self.req({"method": "get_all_accounts"}))[
                "result"
            ]["account_ids"][0]
        return self.account_id

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

    async def get_transactions(self, account_id: str) -> dict[str, dict[str, dict]]:
        return (
            await self.req(
                {
                    "method": "get_all_transaction_logs_for_account",
                    "params": {"account_id": account_id},
                }
            )
        )["result"]["transaction_log_map"]

    async def build_transaction(
            self,
            account_id: str,
            value_pmob: int = -1,
            recipient_public_address: str = "",
            addresses_and_values: Optional[list[tuple[str, int]]] = None,
            input_txo_ids: Optional[list[str]] = None,
            submit: bool=False,
            log: bool =False,
            fee: int = -1,
            tombstone_block: int = -1,
            max_spendable_value: int = -1,
            comment: str = "") -> dict:
        """
        Build a single_txo or multi_txo transaction. If building a single txo
        transaction use value_pmob and recipient_public_address args. If
        building a multi-txo transaction, use the addresses_and_values arg to
        specify a list of [mobilecoin address, amount] pairs.

        args:
          account_id (str): account_id of the sending account
          value_pmob (int): txo value if building a single_txo transaction in picomob
          recipient_public_address (str): address of recipient for single txo transaction
          addresses_and_values (list[tuple[str,int]]): List of pairs of addresses and 
          picomob 
          input_txo_ids: list[str]: list of input_txo_ids to use to build the
          txos. Input txos must be greater than or equal to value being sent in
          the transaction
          submit (bool): if true, transaction will be submitted to mobilecoin
          blockchain
          fee (int): fee to send
          tombstone_block (int): block in which transaction will become invalid
          if not confirmed
          log (bool): Log tx proposal in wallet database
          max_spendable_value (int): max value allowed to spend
          comment (str): comment to annotate transaction purpose to be stored
          in wallet db
        """
        params: dict[str, Any] = {}
        
        method = "build_transaction"
        if submit:
            method = "build_and_submit_transaction"
        if value_pmob > 0:
            params["value_pmob"] = value_pmob
        if recipient_public_address:
            params["recipient_public_address"] = recipient_public_address
        if addresses_and_values:
            params["addresses_and_values"] = addresses_and_values
        if input_txo_ids:
            params["input_txo_ids"] = input_txo_ids
        if submit and comment:
            params["comment"] = comment
        if not submit and log:
            params["log_tx_proposal"] = log
        if fee > 0:
            params["fee"] = fee
        if tombstone_block > 0:
            params["tombstone_block"]  = tombstone_block
        if max_spendable_value >0:
            params["max_spendable_value"] = max_spendable_value
        if comment:
            params["comment"] = comment
        
        return await self.req({"method": method, "params":params})

    async def submit_transaction(
        self, tx_proposal: dict, account_id: str = "", comment: str = ""
    ) -> dict:
        """
        Submit an already built txo. Meant to be used with the output of the
        "build_transaction" method of full service. Do not attempt to build
        your own txo proposal.

        args:
          tx_prop (dict): tx_proposal output from "build_transaction" full service api call
          account_id (str): id of the account the proposal is for
          comment (str): comment to log in wallet account's database

        Return:
          dict: proposal submission result
        """
        request = dict(method="submit_transaction", params=dict(tx_proposal=tx_proposal))

        if account_id:
            request["params"]["account_id"] = account_id  # type: ignore

        if comment:
            request["params"]["comment"] = comment  # type: ignore

        return await self.req(request)

    async def get_all_transaction_logs_by_block(self) -> dict:
        """
        Get all transactions for an account ordered by block

        Returns:
          dict: transaction records ordered by block
        """

        request = dict(method="get_all_transaction_logs_ordered_by_block")
        status = await self.req(request)
        return status.get("result", {})

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

    async def get_wallet_status(self) -> dict:
        """
        Get status of wallet, including block information

        Returns:
          dict: wallet data
        """

        request = dict(method="get_wallet_status")
        return await self.req(request)

    async def get_wallet_balance(self, convert_to_mob: bool = True) -> float:
        """
        Gets current wallet balance, defaults to converting to mob

        args:
          convert_to_mob (bool): convert balance to mob from pmob

        Return:
          float: balance in mob or pmob or -1.0 if error
        """

        data = await self.get_wallet_status()
        wallet_balance = (
            data.get("result", {})
            .get("wallet_status", {})
            .get("total_unspent_pmob", -1)
        )
        try:
            wallet_balance = float(wallet_balance)
            if convert_to_mob:
                wallet_balance = float(mc_util.pmob2mob(wallet_balance))
        except:
            logging.warning("Could not get wallet balance, returning -1.0")
            wallet_balance = -1.0
        return wallet_balance

    async def get_current_network_block(self) -> int:
        """
        Gets current network block, returns -1 if error

        Returns:
          int: Current network block or -1 if error
        """

        data = await self.get_wallet_status()
        start_block = (
            data.get("result", {})
            .get("wallet_status", {})
            .get("network_block_index", -1)
        )
        try:
            start_block = int(start_block)
        except:
            logging.warning("Could not get starting block, returning -1")
            start_block = -1
        return start_block

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

    async def get_all_txos_for_account(self, account_id: str) -> dict:
        """
        Get all txos for account

        args:
          account_id (str): mobilecoin account id

        Returns:
          dict: matching transactions
        """

        txos = await self.req_("get_all_txos_for_account", account_id=account_id)
        return txos.get("result", {}).get("txo_map", {})

    async def filter_utxos(
        self,
        account_id: str,
        txo_status: str = "txo_status_unspent",
        max_val: Union[int, float] = 0,
        min_val: int = 0,
    ) -> list[tuple[str,int]]:
        """
        Get all txos matching specific criterion

        args:
          txo_status (str): status of the txo, specify "txo_status_unspent" to
          get unpsent txos or "txo_status_spent" to get spent txos
          max_val (int): maximum value in pmob
          min_val (int): minimum value in pmob
        """
        if max_val <= 0:
            max_val = float("inf")

        txos = await self.get_all_txos_for_account(account_id)
        utxos = [
            (k, int(v.get("value_pmob")))
            for k, v in txos.items()
            if v.get("account_status_map", {}).get(account_id).get("txo_status")
            == txo_status
            and (max_val > int(v.get("value_pmob")) > min_val)
        ]
        return sorted(utxos, key = lambda txo_value: txo_value[1], reverse=True)

    async def build_split_txo_transaction(self, txo_id: str, output_values:
            list[int], **params: str) -> dict:
        """
        Helper method that splits an existing txo in the account into multiple 
        txos sent to the account. Primary use-case is ensuring enough txos 
        exist to make multiple transactions in a short timeframe.

        args:
          txo_id (str): id of the txo desired to be split
          output_values (list[int]): list o
        """
        values = [str(value) for value in output_values]
        data = dict(method = "build_and_split_txo_transaction", params = dict(
            txo_id=txo_id, output_values=values, **params))
        result = await self.req(data)
        return result.get("result", {})

    async def preallocate_txos(self, account_id: str, tx_list: list[int]) -> None:
        """
        Pre-allocate a list of transactions.
        """
        unspent_utxos = await self.filter_utxos(account_id)
        tx_list = sorted(tx_list, reverse=True)
        total_requested = sum(tx_list)
        total_available = sum([txo[1] for txo in unspent_utxos])
        if total_available < total_requested:
            raise ValueError("Not enough available in account to allocate txos")

    async def create_account(self, name: str) -> dict:
        """
        Create new account in wallet

        args:
          name (str): nickname for the wallet

        Returns:
          dict: new account data
        """

        request = dict(method="create_account", params=dict(name=name))
        return await self.req(request)

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
