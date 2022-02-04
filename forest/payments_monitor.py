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
from copy import deepcopy

import aiohttp
import asyncpg

import mc_util
from forest import utils
from forest.full_service import FullService
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

FEE = 4000000000


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

# pylint: disable=too-many-public-methods
class Mobster(FullService):
    """Convenience Methods for Full Service API"""

    default_url = ()

    def __init__(self, url: str = "") -> None:
        self.ledger_manager = LedgerManager()
        self.invoice_manager = InvoiceManager()
        super().__init__(url)

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

    async def get_address(self, account_id: str = "") -> str:
        """
        Get address for account_id

        args:
          account_id: account id to get address for, if non specified, the
          first account in the wallet is used

        Return:
          str: b58 encoded mobilecoin address. Empty string returned if an
          address is not found for the account id
        """
        if not account_id:
            account_id = await self.get_account_id()
        res = await self.get_all_accounts()
        return (
            res.get("result", {})
            .get("account_map", {})
            .get(account_id, {})
            .get("main_address", "")
        )

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

    async def get_balance(self) -> int:
        """
        Get account balance in pmob
        """
        value = (
            await self.req(
                {
                    "method": "get_balance_for_account",
                    "params": {"account_id": await self.get_account_id()},
                }
            )
        )["result"]["balance"]["unspent_pmob"]
        return int(value)

    async def get_signal_address(self, address: str = "") -> Optional[str]:
        """
        Convert a Mobilecoin b58 address to b64 protobuf address expected by
        Signal when setting a Signal Pay address

        args:
          address (Optional[str]): Address to convert, if left blank the main
          address in the account is used

        Returns
          (str): b64 protobuf address
        """
        main_address = await self.get_address()
        if not address:
            if not main_address:
                logging.warning("no addresses in wallet to convert to signal format")
                return ""
            address = main_address
        return mc_util.b58_wrapper_to_b64_public_address(address)

    # pylint: disable=too-many-arguments
    async def create_transaction(
        self,
        account_id: str = "",
        value_pmob: int = -1,
        recipient_public_address: str = "",
        addresses_and_values: Optional[list[tuple[str, int]]] = None,
        input_txo_ids: Optional[list[str]] = None,
        submit: bool = False,
        log: bool = False,
        comment: str = "",
    ) -> dict:
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
          log (bool): Log tx proposal in wallet database
          comment (str): comment to annotate transaction purpose to be stored
          in wallet db
        """
        if not account_id:
            account_id = await self.get_account_id()
        params: dict[str, Any] = {"account_id": account_id}
        method = "build_transaction"

        if submit:
            method = "build_and_submit_transaction"
        if value_pmob > 0:
            params["value_pmob"] = str(value_pmob)
        if recipient_public_address:
            params["recipient_public_address"] = recipient_public_address
        if addresses_and_values:
            value_pairs = [(v[0], str(v[1])) for v in addresses_and_values]
            params["addresses_and_values"] = value_pairs
        if input_txo_ids:
            params["input_txo_ids"] = input_txo_ids
        if submit and comment:
            params["comment"] = comment
        if not submit and log:
            params["log_tx_proposal"] = log
        if comment:
            params["comment"] = comment

        tx_proposal = {"method": method, "params": params}
        return await self.req(tx_proposal)

    async def get_transactions(self, account_id: str) -> dict[str, dict[str, str]]:
        return (
            await self.req(
                {
                    "method": "get_all_transaction_logs_for_account",
                    "params": {"account_id": account_id},
                }
            )
        )["result"]["transaction_log_map"]

    async def get_current_network_block(self) -> int:
        """
        Gets current network block, returns 0 if error

        Returns:
          int: Current network block or 0 if error
        """

        data = await self.get_wallet_status()
        if not data:
            return 0
        start_block = int(
            (
                data.get("result", {})
                .get("wallet_status", {})
                .get("network_block_height", 0)
            )
        )
        return start_block

    async def get_filtered_txo_list(
        self,
        account_id: str = "",
        txo_status: str = "txo_status_unspent",
        max_val: Union[int, float] = float("inf"),
        min_val: int = 0,
        locked_txos: Optional[dict] = None,
    ) -> list[tuple[str, int]]:
        """
        Get sorted list of (txo_id, amount) pairs fitting specific filters
        """
        txos = await self.get_filtered_txos(
            account_id, txo_status, max_val, min_val, locked_txos
        )
        txo_list = [(k, int(v.get("value_pmob"))) for k, v in txos.items()]
        return sorted(txo_list, key=lambda txo_value: txo_value[1], reverse=True)

    async def get_filtered_txos(
        self,
        account_id: str = "",
        txo_status: str = "txo_status_unspent",
        max_val: Union[int, float] = float("inf"),
        min_val: int = 0,
        locked_txos: Optional[dict] = None,
    ) -> dict:
        """
        Get ordered txos meeting a specific status

        args:
          txo_status (str): status of the txo, specify "txo_status_unspent" to
          get unpsent txos or "txo_status_spent" to get spent txos
          max_val (int): maximum value in pmob
          min_val (int): minimum value in pmob
          locked_txos (dict): txo ids to filter from result

        Returns:
          (list[tuple[str,int]]): ordered list of matching txos
        """
        if not account_id:
            account_id = await self.get_account_id()
        if not locked_txos:
            locked_txos = {}

        txos = await self.get_all_txos_for_account(account_id)
        return {
            k: v
            for k, v in txos.get("result", {}).get("txo_map", {}).items()
            if v.get("account_status_map", {}).get(account_id).get("txo_status")
            == txo_status
            and (max_val > int(v.get("value_pmob")) > min_val)
            and not locked_txos.get(k)
        }

    async def confirm_transaction(self, tx_log_id: str, timeout: int = 10) -> bool:
        """
        Confirm a pending transaction success with timeout

        args:
          tx_log_id (str): transaction log id
          timeout (int): max time to wait for confirmation

        Return
          bool: Transaction success/failure
        """
        tx_data = await self.get_transaction_log(tx_log_id)
        if not isinstance(tx_data, dict) or not "result" in tx_data:
            return False

        count = 0
        while count < timeout:
            tx_log = tx_data.get("result", {}).get("transaction_log", {})
            logging.info(tx_log)
            status = tx_log.get("status")
            if status == "tx_status_succeeded":
                return True
            if status == "tx_status_failed":
                logging.warning("tx failed, log: %s", tx_log)
                return False
            await asyncio.sleep(1)
            count += 1
        if count >= timeout:
            logging.warning("Failed to confirm transaction in %s tries", timeout)
        return False

    async def cleanup_utxos(
        self,
        max_single_txo: int,
        account_id: str = "",
        locked_txos: Optional[dict[str, Any]] = None,
        largest_first: bool = False,
    ) -> list[tuple[str, int]]:
        """
        Consolidate wallet txos into a single txo of specified size

        args:
          acount_id (str): Mobilecoin wallet account_id
          max_single_txo (int): Size (in pmob) of transaction to consolidate to
          locked_txos (dict[str, Any]): dict of txo_ids to exclude from cleanup
        """

        logging.info("cleaning up transactions to ceiling of: %s", max_single_txo)
        if not account_id:
            account_id = await self.get_account_id()
        address = await self.get_address()
        retries = 0
        while retries < 2:
            utxos = await self.get_filtered_txo_list(
                account_id, locked_txos=locked_txos
            )
            if sum([txo[1] for txo in utxos]) - FEE <= max_single_txo:
                logging.warning("free utxos less than requested, aborting")
                return utxos

            txo_slice = utxos[16:]
            if largest_first:
                txo_slice = utxos[:16]

            tail_amt = sum([txo[1] for txo in txo_slice])
            chosen_utxos = txo_slice
            if tail_amt - FEE > max_single_txo:
                tail_amt, chosen_utxos = 0, []
                for utxo in reversed(txo_slice):
                    tail_amt += utxo[1]
                    if tail_amt - FEE > max_single_txo:
                        break
                    chosen_utxos.append(utxo)

            logging.debug("selected utxos for cleanup %s", txo_slice)
            if len(chosen_utxos) > 1:
                prop = await self.create_transaction(
                    account_id,
                    tail_amt - FEE,
                    address,
                    input_txo_ids=[txo[0] for txo in chosen_utxos],
                    submit=True,
                    comment="utxo_cleanup",
                )
            tx_log_id = (
                prop.get("result", {})
                .get("transaction_log", {})
                .get("transaction_log_id", "")
            )
            if not await self.confirm_transaction(tx_log_id):
                retries += 1
            if tail_amt - FEE > max_single_txo:
                return await self.get_filtered_txo_list(account_id)
        logging.warning("Txo condensation failed too many times, aborting")
        return await self.get_filtered_txo_list(account_id)

    async def preallocate_txos(
        self,
        account_id: str,
        txo_list: list[int],
        locked_txos: Optional[dict[str, Any]] = None,
        address: str = "",
    ) -> dict[str, int]:
        #pylint: disable = too-many-branches disable=too-many-locals
        #pylint: disable = too-many-statements disable=too-many-nested-blocks
        """
        Pre-allocate utxos in exact amount for a list of amounts. Used when a
        large amount of transactions need to be sent.

        args:
          account_id (str): account_id of the account to allocate the txos within
          txo_list (list[int]): list of transaction amounts in pmob to allocate
          locked_txos (dict[str, Any]): dict of txo_ids that should not be
          used as inputs for txo_pre_allocation. key of this dict should be the
          txo_id
          address (str): address to send txo outputs to (default: main address of
          account)

        """
        _locked_txos = {}
        if isinstance(locked_txos, dict):
            _locked_txos = deepcopy(locked_txos)
        if not address:
            address = await self.get_address(account_id=account_id)

        unspent_utxos = await self.get_filtered_txo_list(account_id, locked_txos=_locked_txos)
        txo_list = sorted(txo_list, reverse=True)
        split_txos = [txo_list[i : i + 15] for i in range(0, len(txo_list), 15)]
        free = sum([txo[1] for txo in unspent_utxos])
        requested = sum(txo_list) + FEE * (len(txo_list) + len(split_txos) + 1)
        free_largest = [txo[1] for txo in unspent_utxos[: len(txo_list)]]

        logging.info("attempting pre_allocation of utxos: %s", txo_list)
        if free < requested:
            logging.warning("requested/free: %s/%s can't allocate", requested, free)
            return {}
        if sum(free_largest) < requested:
            logging.info(
                    "Largest %s txos totaled %s, requested %s\n\nlargest txos: %s",
                len(free_largest), sum(free_largest), requested, free_largest
            )
            await self.cleanup_utxos(
                requested, account_id, locked_txos=_locked_txos, largest_first=True
            )
            unspent_utxos = await self.get_filtered_txo_list(
                account_id, locked_txos=_locked_txos
            )
        output_txos = []

        for sublist in split_txos:
            utxo_inputs = []
            txo_total = sum(sublist) + FEE * len(sublist)
            logging.info("Allocating %s txos totaling %s Pmob", len(sublist), txo_total)
            retries = 0
            while retries <= 2:
                for utxo in unspent_utxos:
                    utxo_inputs.append(utxo)
                    logging.debug("utxo_inputs are %s", utxo_inputs)
                    if sum([utxo[1] for utxo in utxo_inputs]) >= txo_total:
                        try:
                            tx_prop = await self.create_transaction(
                                account_id,
                                addresses_and_values=[(address, amt + FEE) for amt in sublist],
                                input_txo_ids=[utxo[0] for utxo in utxo_inputs],
                                submit=True,
                                comment="txo_allocation",
                            )
                        except Exception as e: # pylint: disable=broad-except
                            logging.warning("aiohttp connection error %s on retry %s, retrying", e, retries)
                            retries += 1
                            break
                        tx_log = tx_prop.get("result", {}).get("transaction_log",{})
                        if "error" in tx_prop or not isinstance(tx_log.get("output_txos"), list):
                            if "error" in tx_prop:
                                logging.warning("full service api error on retry %s - error: %s",
                                    retries, tx_prop)
                            else:
                                logging.warning("no output txos detected on retry %s, retrying", retries)
                            retries += 1
                            break
                        confirmation = await self.confirm_transaction(tx_log.get("transacton_log_id",""))
                        logging.info("transaction successful: %s", confirmation)
                        if not confirmation:
                            logging.warning("allocation confirmation failed on retry %s, retrying", retries)
                            retries += 1
                            break
                        output_txos.extend(
                            [
                                (utxo.get("txo_id_hex"), int(utxo.get("value_pmob")))
                                for utxo in tx_prop.get("result", {}).get("transaction_log", {}).get("output_txos")
                            ]
                        )
                        _locked_txos.update(dict(output_txos))
                        logging.debug("new locked txos are: %s", _locked_txos)
                        retries = 1000
                        break
                unspent_utxos = await self.get_filtered_txo_list(
                    account_id, locked_txos=_locked_txos
                )
                logging.debug("new unspent_txos are %s", unspent_utxos)
        return {utxo[0]: utxo[1] for utxo in output_txos}

    async def monitor_wallet(self) -> None:
        last_transactions: dict[str, dict[str, str]] = {}
        account_id = await self.get_account_id()
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
