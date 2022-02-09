# pylint: disable=too-many-arguments disable=too-many-public-methods
import json
import logging
import ssl
import base64
from typing import Optional, Any
import aiohttp
from forest.utils import get_secret

if not get_secret("ROOTCRT"):
    ssl_context: Optional[ssl.SSLContext] = None
else:
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    root = open("rootcrt.pem", "wb")
    root.write(base64.b64decode(get_secret("ROOTCRT")))
    root.flush()
    client = open("client.full.pem", "wb")
    client.write(base64.b64decode(get_secret("CLIENTCRT")))
    client.flush()

    ssl_context.load_verify_locations("rootcrt.pem")
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    ssl_context.load_cert_chain(certfile="client.full.pem")


class FullService:
    """
    Asynchronous python wrapper around the MobileCoin Full Service API
    """

    def __init__(self, url: str = "") -> None:
        if not url:
            url = get_secret("FULL_SERVICE_URL") or "http://localhost:9090/wallet"
        self.account_id: str = ""
        self.url = url
        logging.info("full-service url: %s", url)

    async def req_(self, method: str, **params: Any) -> dict:
        _params = {k: v for k, v in params.items() if v}
        result = await self.req({"method": method, "params": _params})
        if "error" in result:
            logging.error(result)
        return result

    async def req(self, data: dict) -> dict:
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

    async def get_account_id(self, name: str = "", index: int = 0) -> str:
        """
        Get account id from list of accounts

        args:
          name (str): Name of the account
          index (int): Index of account in account list

        Returns:
          str: unique identifier for account
        """

        if (name or index) or not self.account_id:
            accounts = await self.req({"method": "get_all_accounts"})
            if name:
                _id = [
                    acct["account_id"]
                    for acct in accounts.get("result", {})
                    .get("account_map", {})
                    .values()
                    if acct["name"] == name
                ]
                if _id:
                    return _id[0]
                return ""
            account_ids = accounts.get("result", {}).get("account_ids", [])
            if len(account_ids) >= index + 1:
                self.account_id = account_ids[index]
        return self.account_id

    async def assign_address_for_account(
        self,
        account_id: str = "",
        metadata: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="assign_address_for_account",
            account_id=account_id,
            metadata=metadata,
        )

    async def build_and_submit_transaction(
        self,
        account_id: str = "",
        addresses_and_values: Optional[list[tuple[str, str]]] = None,
        recipient_public_address: Optional[str] = None,
        value_pmob: Optional[str] = None,
        input_txo_ids: Optional[list[str]] = None,
        fee: Optional[str] = None,
        tombstone_block: Optional[str] = None,
        max_spendable_value: Optional[str] = None,
        comment: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="build_and_submit_transaction",
            account_id=account_id,
            addresses_and_values=addresses_and_values,
            recipient_public_address=recipient_public_address,
            value_pmob=value_pmob,
            input_txo_ids=input_txo_ids,
            fee=fee,
            tombstone_block=tombstone_block,
            max_spendable_value=max_spendable_value,
            comment=comment,
        )

    async def build_gift_code(
        self,
        account_id: str = "",
        value_pmob: str = "",
        memo: Optional[str] = None,
        input_txo_ids: Optional[list[str]] = None,
        fee: Optional[str] = None,
        tombstone_block: Optional[str] = None,
        max_spendable_value: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="build_gift_code",
            account_id=account_id,
            value_pmob=value_pmob,
            memo=memo,
            input_txo_ids=input_txo_ids,
            fee=fee,
            tombstone_block=tombstone_block,
            max_spendable_value=max_spendable_value,
        )

    async def build_split_txo_transaction(
        self,
        output_values: list[str],
        txo_id: str = "",
        destination_subaddress_index: Optional[str] = None,
        fee: Optional[str] = None,
        tombstone_block: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="build_split_txo_transaction",
            txo_id=txo_id,
            output_values=output_values,
            destination_subaddress_index=destination_subaddress_index,
            fee=fee,
            tombstone_block=tombstone_block,
        )

    async def build_transaction(
        self,
        account_id: str = "",
        addresses_and_values: Optional[list[tuple[str, str]]] = None,
        recipient_public_address: Optional[str] = None,
        value_pmob: Optional[str] = None,
        input_txo_ids: Optional[list[str]] = None,
        fee: Optional[str] = None,
        tombstone_block: Optional[str] = None,
        max_spendable_value: Optional[str] = None,
        log_tx_proposal: Optional[bool] = None,
    ) -> dict:

        return await self.req_(
            method="build_transaction",
            account_id=account_id,
            addresses_and_values=addresses_and_values,
            recipient_public_address=recipient_public_address,
            value_pmob=value_pmob,
            input_txo_ids=input_txo_ids,
            fee=fee,
            tombstone_block=tombstone_block,
            max_spendable_value=max_spendable_value,
            log_tx_proposal=log_tx_proposal,
        )

    async def check_b58_type(
        self,
        b58_code: str = "",
    ) -> dict:

        return await self.req_(
            method="check_b58_type",
            b58_code=b58_code,
        )

    async def check_gift_code_status(
        self,
        gift_code_b58: str = "",
    ) -> dict:

        return await self.req_(
            method="check_gift_code_status",
            gift_code_b58=gift_code_b58,
        )

    async def check_receiver_receipt_status(
        self,
        receiver_receipt: dict,
        address: str = "",
    ) -> dict:

        return await self.req_(
            method="check_receiver_receipt_status",
            address=address,
            receiver_receipt=receiver_receipt,
        )

    async def claim_gift_code(
        self,
        gift_code_b58: str = "",
        account_id: str = "",
        address: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="claim_gift_code",
            gift_code_b58=gift_code_b58,
            account_id=account_id,
            address=address,
        )

    async def create_account(
        self,
        name: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="create_account",
            name=name,
        )

    async def create_payment_request(
        self,
        account_id: str = "",
        subaddress_index: Optional[int] = None,
        amount_pmob: int = 0,
        memo: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="create_payment_request",
            account_id=account_id,
            subaddress_index=subaddress_index,
            amount_pmob=amount_pmob,
            memo=memo,
        )

    async def create_receiver_receipts(
        self,
        tx_proposal: dict,
    ) -> dict:

        return await self.req_(
            method="create_receiver_receipts",
            tx_proposal=tx_proposal,
        )

    async def export_account_secrets(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="export_account_secrets",
            account_id=account_id,
        )

    async def get_account(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_account",
            account_id=account_id,
        )

    async def get_account_status(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_account_status",
            account_id=account_id,
        )

    async def get_address_for_account(
        self,
        account_id: str = "",
        index: int = 0,
    ) -> dict:

        return await self.req_(
            method="get_address_for_account",
            account_id=account_id,
            index=index,
        )

    async def get_addresses_for_account(
        self,
        account_id: str = "",
        offset: str = "",
        limit: str = "",
    ) -> dict:

        return await self.req_(
            method="get_addresses_for_account",
            account_id=account_id,
            offset=offset,
            limit=limit,
        )

    async def get_all_accounts(self) -> dict:
        return await self.req({"method": "get_all_accounts"})

    async def get_all_addresses_for_account(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_all_addresses_for_account",
            account_id=account_id,
        )

    async def get_all_gift_codes(self) -> dict:
        return await self.req({"method": "get_all_gift_codes"})

    async def get_all_transaction_logs_for_account(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_all_transaction_logs_for_account",
            account_id=account_id,
        )

    async def get_all_transaction_logs_for_block(
        self,
        block_index: str = "",
    ) -> dict:

        return await self.req_(
            method="get_all_transaction_logs_for_block",
            block_index=block_index,
        )

    async def get_all_transaction_logs_ordered_by_block(self) -> dict:
        return await self.req({"method": "get_all_transaction_logs_ordered_by_block"})

    async def get_all_txos_for_account(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_all_txos_for_account",
            account_id=account_id,
        )

    async def get_all_txos_for_address(
        self,
        address: str = "",
    ) -> dict:

        return await self.req_(
            method="get_all_txos_for_address",
            address=address,
        )

    async def get_balance_for_account(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_balance_for_account",
            account_id=account_id,
        )

    async def get_balance_for_address(
        self,
        address: str = "",
    ) -> dict:

        return await self.req_(
            method="get_balance_for_address",
            address=address,
        )

    async def get_block(
        self,
        block_index: str = "",
    ) -> dict:

        return await self.req_(
            method="get_block",
            block_index=block_index,
        )

    async def get_confirmations(
        self,
        transaction_log_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_confirmations",
            transaction_log_id=transaction_log_id,
        )

    async def get_gift_code(
        self,
        gift_code_b58: str = "",
    ) -> dict:

        return await self.req_(
            method="get_gift_code",
            gift_code_b58=gift_code_b58,
        )

    async def get_mc_protocol_transaction(
        self,
        transaction_log_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_mc_protocol_transaction",
            transaction_log_id=transaction_log_id,
        )

    async def get_mc_protocol_txo(
        self,
        txo_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_mc_protocol_txo",
            txo_id=txo_id,
        )

    async def get_network_status(self) -> dict:
        return await self.req({"method": "get_network_status"})

    async def get_transaction_log(
        self,
        transaction_log_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_transaction_log",
            transaction_log_id=transaction_log_id,
        )

    async def get_transaction_logs_for_account(
        self,
        account_id: str = "",
        offset: str = "",
        limit: str = "",
    ) -> dict:

        return await self.req_(
            method="get_transaction_logs_for_account",
            account_id=account_id,
            offset=offset,
            limit=limit,
        )

    async def get_txo(
        self,
        txo_id: str = "",
    ) -> dict:

        return await self.req_(
            method="get_txo",
            txo_id=txo_id,
        )

    async def get_txos_for_account(
        self,
        account_id: str = "",
        offset: str = "",
        limit: str = "",
    ) -> dict:

        return await self.req_(
            method="get_txos_for_account",
            account_id=account_id,
            offset=offset,
            limit=limit,
        )

    async def get_wallet_status(self) -> dict:
        return await self.req({"method": "get_wallet_status"})

    async def import_account(
        self,
        mnemonic: str = "",
        key_derivation_version: str = "",
        name: Optional[str] = None,
        first_block_index: Optional[str] = None,
        next_subaddress_index: Optional[str] = None,
        fog_report_url: Optional[str] = None,
        fog_report_id: Optional[str] = None,
        fog_authority_spki: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="import_account",
            mnemonic=mnemonic,
            key_derivation_version=key_derivation_version,
            name=name,
            first_block_index=first_block_index,
            next_subaddress_index=next_subaddress_index,
            fog_report_url=fog_report_url,
            fog_report_id=fog_report_id,
            fog_authority_spki=fog_authority_spki,
        )

    async def import_account_from_legacy_root_entropy(
        self,
        entropy: str = "",
        name: Optional[str] = None,
        first_block_index: Optional[str] = None,
        next_subaddress_index: Optional[str] = None,
        fog_report_url: Optional[str] = None,
        fog_report_id: Optional[str] = None,
        fog_authority_spki: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="import_account_from_legacy_root_entropy",
            entropy=entropy,
            name=name,
            first_block_index=first_block_index,
            next_subaddress_index=next_subaddress_index,
            fog_report_url=fog_report_url,
            fog_report_id=fog_report_id,
            fog_authority_spki=fog_authority_spki,
        )

    async def remove_account(
        self,
        account_id: str = "",
    ) -> dict:

        return await self.req_(
            method="remove_account",
            account_id=account_id,
        )

    async def remove_gift_code(
        self,
        gift_code_b58: str = "",
    ) -> dict:

        return await self.req_(
            method="remove_gift_code",
            gift_code_b58=gift_code_b58,
        )

    async def submit_gift_code(
        self,
        tx_proposal: dict,
        from_account_id: str = "",
        gift_code_b58: str = "",
    ) -> dict:

        return await self.req_(
            method="submit_gift_code",
            from_account_id=from_account_id,
            gift_code_b58=gift_code_b58,
            tx_proposal=tx_proposal,
        )

    async def submit_transaction(
        self,
        tx_proposal: dict,
        comment: Optional[str] = None,
        account_id: Optional[str] = None,
    ) -> dict:

        return await self.req_(
            method="submit_transaction",
            tx_proposal=tx_proposal,
            comment=comment,
            account_id=account_id,
        )

    async def update_account_name(
        self,
        account_id: str = "",
        name: str = "",
    ) -> dict:

        return await self.req_(
            method="update_account_name",
            account_id=account_id,
            name=name,
        )

    async def validate_confirmation(
        self,
        account_id: str = "",
        txo_id: str = "",
        confirmation: str = "",
    ) -> dict:

        return await self.req_(
            method="validate_confirmation",
            account_id=account_id,
            txo_id=txo_id,
            confirmation=confirmation,
        )

    async def verify_address(
        self,
        address: str = "",
    ) -> dict:

        return await self.req_(
            method="verify_address",
            address=address,
        )
