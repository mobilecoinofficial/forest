#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
# pylint: disable=invalid-name disable=line-too-long disable=missing-module-docstring disable=consider-using-with
# pylint: disable=too-many-locals disable=too-many-arguments disable=bare-except
# pylint: disable=consider-using-enumerate
import asyncio
import base64
import json
import logging
import random
import ssl
import time
from copy import deepcopy
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
FEE = 400000000
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
    """
    Manage simple list of invoices
    """

    def __init__(self) -> None:
        super().__init__(InvoicePGEExpressions, DATABASE_URL, None)


class LedgerManager(PGInterface):
    """
    Manage simple list of txos
    """

    def __init__(
        self,
        queries: PGExpressions = LedgerPGExpressions,
        database: str = DATABASE_URL,
        loop: Loop = None,
    ) -> None:
        super().__init__(queries, database, loop)


def delete_indices(list_object: list, indices: list[int]) -> None:
    """
    Delete elements of specific list
    """
    indices = sorted(indices, reverse=True)
    for idx in indices:
        if idx < len(list_object):
            list_object.pop(idx)


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


class Mobster:  # pylint: disable=too-many-public-methods
    """Class to keep track of aiohttp session and provide useful full service
    api methods"""

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
        """
        Make json rpc request to MobileCoin full-service api specifying
        arguments as named parameters
        """
        logging.info(
            "full-service request: {method: %s, params: %s}",
            method,
            list(params.keys()),
        )
        result = await self.req({"method": method, "params": params})
        if "error" in result:
            logging.error(result)
        return result

    async def req(self, data: dict) -> dict:
        """
        Make json rpc request to Mobilecoin full-service api using a dictionary
        """
        better_data = {"jsonrpc": "2.0", "id": 1, **data}
        async with aiohttp.TCPConnector(ssl=ssl_context) as conn:
            async with aiohttp.ClientSession(connector=conn) as sess:
                mob_req = sess.post(
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
        """
        Convert pmob into current market rate usd

        args:
          pmob (int): amount in pmob to convert

        Returns:
          float: amount in USD
        """
        return float(mc_util.pmob2mob(pmob)) * await self.get_rate()

    async def usd2mob(self, usd: float, perturb: bool = False) -> float:
        """
        Convert current market rate of USD into MoB

        args:
          usd (float): amount in USD to convert
          peturb (bool): extend precision to 8 floating points
        """
        mob_rate = await self.get_rate()
        if perturb:
            # perturb each price slightly to have a unique payment
            mob_rate -= random.random() / 1000
        mob_amount = usd / mob_rate
        if perturb:
            return round(mob_amount, 8)
        return round(mob_amount, 3)  # maybe ceil?

    async def create_invoice(self, amount_usd: float, account: str, memo: str) -> float:
        """
        Create invoice using full service api
        """
        while 1:
            try:
                mob_price_exact = await self.usd2mob(amount_usd, perturb=True)
                await self.invoice_manager.create_invoice(
                    account, mc_util.mob2pmob(mob_price_exact), memo
                )
                return mob_price_exact
            except asyncpg.UniqueViolationError:
                pass

    async def is_txo_unspent(self, txo_id: str) -> bool:
        """
        Check if txo is unspent

        args:
            txo_id (str): unique hash identifying the txo

        Returns:
          bool: Boolean indicating if txo exists
        """
        txo = await self.get_txo(txo_id)
        if not txo:
            return False
        account_id = txo.get("txo", {}).get("received_account_id")
        status = (
            txo.get("txo", {})
            .get("account_status_map", {})
            .get(account_id, {})
            .get("txo_status")
        )
        if status == "txo_status_unspent":
            return True
        return False

    async def import_account(self) -> dict:
        """
        import mobilecoin account with an account mnemonic
        """
        params = {
            "mnemonic": utils.get_secret("MNEMONIC"),
            "key_derivation_version": "1",
            "name": "falloopa",
            "next_subaddress_index": "2",
            "first_block_index": "3500",
        }
        return await self.req({"method": "import_account", "params": params})

    # cache?
    async def get_address(self, index: int = 0, account_id: str = "") -> str:
        """
        Get main address in wallet for a specific account
        """
        if account_id:
            res = await self.req_("get_account", account_id=account_id)
            return res.get("result", {}).get("account", {}).get("main_address", "")
        res = await self.req({"method": "get_all_accounts"})
        acc_id = res["result"]["account_ids"][index]
        return res["result"]["account_map"][acc_id]["main_address"]

    async def get_receipt_amount_pmob(self, receipt_str: str) -> Optional[int]:
        """
        Use blinded full service receipt to get amount of sent transaction

        receipt_str (str): receipt string

        Returns (Optional[int]): amount of transaction specified in receipt
        """
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

    async def get_account(self, index: int = 0) -> str:
        """
        Get account_id of an account within the wallet in use
        """
        if not isinstance(self.account_id, str):
            self.account_id = (await self.req({"method": "get_all_accounts"}))[
                "result"
            ]["account_ids"][index]
        return self.account_id

    async def get_balance(self) -> int:
        """
        Get balance of primary account in wallet in pmob

        Returns:
          int: balance in pmob
        """
        value = (
            await self.req(
                {
                    "method": "get_balance_for_account",
                    "params": {"account_id": await self.get_account()},
                }
            )
        )["result"]["balance"]["unspent_pmob"]
        return int(value)

    async def get_txo(self, txo_id: str) -> dict:
        """
        Get data on specific transaction output (txo) id

        args:
          txo_id (str): id of the transaction output to examine

        Returns:
          dict: information about txo
        """
        result = await self.req_("get_txo", txo_id=txo_id)
        if isinstance(result, dict):
            if result.get("result"):
                return result["result"]
            return result

    async def get_transactions(self, account_id: str) -> dict[str, dict[str, dict]]:
        """
        Get all transactions logs for a specified account

        args:
          account_id (str): account_id of desired account

        Returns:
          dict[str, dict[str,dict]]: list of transactions & associated metadata
        """
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
        submit: bool = False,
        log: bool = False,
        fee: int = -1,
        tombstone_block: int = -1,
        max_spendable_value: int = -1,
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
          fee (int): fee to send
          tombstone_block (int): block in which transaction will become invalid
          if not confirmed
          log (bool): Log tx proposal in wallet database
          max_spendable_value (int): max value allowed to spend
          comment (str): comment to annotate transaction purpose to be stored
          in wallet db
        """
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
        if fee > 0:
            params["fee"] = fee
        if tombstone_block > 0:
            params["tombstone_block"] = tombstone_block
        if max_spendable_value > 0:
            params["max_spendable_value"] = max_spendable_value
        if comment:
            params["comment"] = comment

        tx_proposal = {"method": method, "params": params}
        logging.info("building txo_proposal: %s", tx_proposal)
        return await self.req(tx_proposal)

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
        request = dict(
            method="submit_transaction", params=dict(tx_proposal=tx_proposal)
        )

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

    async def get_filtered_balance(self, locked_txos: dict[str, Any]) -> int:
        """
        Return of wallet minus already locked transactions

        args:
          locked_txos (dict): Dict of txo_ids of locked transactions to filter
          out of the totalremove from result

        Returns:
          int: wallet balance in pmob minus filtered transactions
        """
        account_id = await self.get_account()
        utxos = await self.filter_utxos(account_id, locked_txos=locked_txos)

        return sum([utxo[1] for utxo in utxos])

    async def get_transaction_log(self, transaction_log_id: str) -> dict:
        logging.info("transaction log id is %s", transaction_log_id)
        return await self.req_(
            "get_transaction_log", transaction_log_id=transaction_log_id
        )

    async def filter_utxos(
        self,
        account_id: str,
        txo_status: str = "txo_status_unspent",
        max_val: Union[int, float] = 0,
        min_val: int = 0,
        locked_txos: Optional[dict] = None,
    ) -> list[tuple[str, int]]:
        """
        Get all txos matching specific criterion

        args:
          txo_status (str): status of the txo, specify "txo_status_unspent" to
          get unpsent txos or "txo_status_spent" to get spent txos
          max_val (int): maximum value in pmob
          min_val (int): minimum value in pmob
          locked_txos (dict): Dict of txo_ids to remove from result. Dict
          keys should be txo_id hashes that are desired to remove.
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
        unspent_utxos = sorted(utxos, key=lambda txo_value: txo_value[1], reverse=True)
        if isinstance(locked_txos, dict):
            txs_to_remove = []
            for i in range(len(unspent_utxos)):
                if locked_txos.get(unspent_utxos[i][0]):
                    txs_to_remove.append(i)
            delete_indices(unspent_utxos, txs_to_remove)
        return unspent_utxos

    async def build_split_txo_transaction(
        self, txo_id: str, output_values: list[int], **params: str
    ) -> dict:
        """
        Helper method that splits an existing txo in the account into multiple
        txos sent to the account. Primary use-case is ensuring enough txos
        exist to make multiple transactions in a short timeframe.

        args:
          txo_id (str): id of the txo desired to be split
          output_values (list[int]): list o
        """
        values = [str(value) for value in output_values]
        data = dict(
            method="build_and_split_txo_transaction",
            params=dict(txo_id=txo_id, output_values=values, **params),
        )
        result = await self.req(data)
        return result.get("result", {})

    async def cleanup_utxos(
        self,
        account_id: str,
        max_single_txo: int,
        locked_txos: Optional[dict[str, Any]] = None,
        largest_first: bool = False,
    ) -> list[tuple[str, int]]:
        """
        Consolidate wallet txos from smallest to largest into a single txo of
        specified size

        args:
          acount_id (str): Mobilecoin wallet account_id
          max_single_txo (int): Size (in pmob) of transaction to consolidate to
          locked_txos (dict[str, Any]): dict of txo_ids that should not be
          used as inputs for txo_pre_allocation. key of this dict should be the
          txo_id
        """
        logging.info("cleaning up transactions to ceiling of: %s", max_single_txo)
        logging.info("locked utxos for cleanup %s", locked_txos)
        logging.info("direction largest_first %s", largest_first)
        addy = await self.get_address(account_id=account_id)
        while True:
            utxos = await self.filter_utxos(account_id, locked_txos=locked_txos)
            if largest_first:
                txo_slice = utxos[:16]
            else:
                txo_slice = utxos[-16:]
            free_amount = sum([txo[1] for txo in utxos])
            if free_amount - FEE <= max_single_txo:
                logging.warning(
                    "Non-reserved utxos total: %s is less than requested consolidation %s, cannot allocate",
                    free_amount - FEE,
                    max_single_txo,
                )
                return utxos

            tail_amt = sum([txo[1] for txo in txo_slice])
            if tail_amt - FEE > max_single_txo:
                chosen_utxos = []
                tail_amt = 0
                for utxo in reversed(txo_slice):
                    if utxo[1] >= max_single_txo:
                        break
                    chosen_utxos.append(utxo)
                    tail_amt = sum([txo[1] for txo in chosen_utxos])
                    if tail_amt - FEE > max_single_txo:
                        break
                logging.info("Final txos to be cleaned %s", txo_slice)
                if tail_amt > 0 and len(chosen_utxos) > 1:
                    tx_prop = await self.build_transaction(
                        account_id,
                        tail_amt - FEE,
                        addy,
                        input_txo_ids=[txo[0] for txo in chosen_utxos],
                        submit=True,
                        comment="utxo_cleanup",
                    )

                    tx_log = (
                        tx_prop.get("result", {})
                        .get("transaction_log")
                        .get("transaction_log_id", "")
                    )
                    confirmation = await self.confirm_transaction(tx_log)
                final_utxo_state = await self.filter_utxos(account_id)
                logging.info("final state of utxos in wallet %s", final_utxo_state)
                return final_utxo_state

            logging.debug("selected utxos for cleanup %s", txo_slice)
            tx_prop = await self.build_transaction(
                account_id,
                tail_amt - FEE,
                addy,
                input_txo_ids=[txo[0] for txo in txo_slice],
                submit=True,
                comment="utxo_cleanup",
            )
            # Sleep to make sure full service doesn't eat transactions??
            tx_log = (
                tx_prop.get("result", {})
                .get("transaction_log")
                .get("transaction_log_id", "")
            )
            confirmation = await self.confirm_transaction(tx_log)

    async def confirm_transaction(self, tx_log_id: str, timeout: int = 20) -> bool:
        """
        Confirm transaction success

        args:
          tx_log_id (str): transaction log id
          timeout (int)

        Return
          bool: Transaction success
        """
        txo_data = await self.get_transaction_log(tx_log_id)
        if not isinstance(txo_data, dict) or "error" in txo_data:
            return False

        time = 0
        while time < timeout:
            status = txo_data.get("result", {}).get("transaction_log", {}).get("status")
            if status == "tx_status_succeeded":
                return True
            if status == "tx_status_failed":
                logging.warning(
                    "transaction failed, log: %s",
                    txo_data.get("result", {}).get("transaction_log", {}),
                )
                return False
            await asyncio.sleep(1)
            txo_data = await self.get_transaction_log(tx_log_id)
            time += 1
        if time >= timeout:
            logging.warning("Failed to confirm transaction in %s tries", timeout)
        return False

    async def preallocate_txos(
        self,
        account_id: str,
        txo_list: list[int],
        locked_txos: Optional[dict[str, Any]] = None,
        address: str = "",
    ) -> dict[str, int]:
        """
        Pre-allocate UTXOS in exact amount for a list of amounts. Used when a
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

        unspent_utxos = await self.filter_utxos(account_id, locked_txos=_locked_txos)
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
                len(free_largest),
                sum(free_largest),
                requested,
                free_largest,
            )
            await self.cleanup_utxos(
                account_id, requested, locked_txos=_locked_txos, largest_first=True
            )
            unspent_utxos = await self.filter_utxos(
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
                            tx_prop = await self.build_transaction(
                                account_id,
                                addresses_and_values=[
                                    (address, amt + FEE) for amt in sublist
                                ],
                                input_txo_ids=[utxo[0] for utxo in utxo_inputs],
                                submit=True,
                                comment="txo_allocation",
                            )
                        except Exception as e:  # pylint: disable=broad-except
                            logging.warning(
                                "aiohttp connection error %s on retry %s, retrying",
                                e,
                                retries,
                            )
                            retries += 1
                            break
                        tx_log = tx_prop.get("result", {}).get("transaction_log", {})
                        if "error" in tx_prop or not isinstance(
                            tx_log.get("output_txos"), list
                        ):
                            if "error" in tx_prop:
                                logging.warning(
                                    "full service api error on retry %s - error: %s",
                                    retries,
                                    tx_prop,
                                )
                            else:
                                logging.warning(
                                    "no output txos detected on retry %s, retrying",
                                    retries,
                                )
                            retries += 1
                            break
                        confirmation = await self.confirm_transaction(
                            tx_log.get("transaction_log_id", "")
                        )
                        logging.info("transaction successful: %s", confirmation)
                        if not confirmation:
                            logging.warning(
                                "allocation confirmation failed on retry %s, retrying",
                                retries,
                            )
                            retries += 1
                            break
                        output_txos.extend(
                            [
                                (utxo.get("txo_id_hex"), int(utxo.get("value_pmob")))
                                for utxo in tx_prop.get("result", {})
                                .get("transaction_log", {})
                                .get("output_txos")
                            ]
                        )
                        _locked_txos.update(dict(output_txos))
                        logging.debug("new locked txos are: %s", _locked_txos)
                        retries = 1000
                        break
                unspent_utxos = await self.filter_utxos(
                    account_id, locked_txos=_locked_txos
                )
                logging.debug("new unspent_txos are %s", unspent_utxos)
        return {utxo[0]: utxo[1] for utxo in output_txos}

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
        """
        Monitor wallet for transactions and store transactions in postgres
        database
        """
        last_transactions: dict[str, dict[Any, Any]] = {}
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
                    assert isinstance(short_tx["value_pmob"], int)
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
