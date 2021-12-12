# pylint: skip-file
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
from decimal import Decimal
from typing import Union, Optional
import zlib
import base64

import base58

from . import external_pb2
from . import printable_pb2


PMOB = Decimal("1e12")


def mob2pmob(x: Union[Decimal, float]) -> int:
    """Convert from MOB to picoMOB."""
    return round(Decimal(x) * PMOB)


def pmob2mob(x: int) -> Decimal:
    """Convert from picoMOB to MOB."""
    result = int(x) / PMOB
    if result == 0:
        return Decimal("0")
    else:
        return result


def b64_public_address_to_b58_wrapper(b64_string: str) -> str:
    """Convert a b64-encoded PublicAddress protobuf to a b58-encoded PrintableWrapper protobuf"""
    public_address_bytes = base64.b64decode(b64_string)

    public_address = external_pb2.PublicAddress()
    public_address.ParseFromString(public_address_bytes)

    wrapper = printable_pb2.PrintableWrapper()
    wrapper.public_address.CopyFrom(public_address)

    wrapper_bytes = wrapper.SerializeToString()

    checksum = zlib.crc32(wrapper_bytes)
    checksum_bytes = checksum.to_bytes(4, byteorder="little")

    checksum_and_wrapper_bytes = checksum_bytes + wrapper_bytes

    return base58.b58encode(checksum_and_wrapper_bytes).decode("utf-8")


# via https://github.com/mobilecoinfoundation/mobilecoin/blob/master/api/proto/printable.proto
# message TransferPayload
# > Message encoding a private key and a UTXO, for the purpose of
# > giving someone access to an output. This would most likely be
# > used for gift cards.
# message PrintableWrapper
# > This wraps [external.PublicAddress, payment_request, TransferPayload] using "oneof", allowing us to
# > have a single encoding scheme and extend as necessary simply by adding
# > new messages without breaking backwards compatibility

# this can be used to import a gift card's entropy into full-service
def b58_wrapper_to_protobuf(
    b58_string: str,
) -> Optional[printable_pb2.PrintableWrapper]:
    """Convert a b58-encoded PrintableWrapper into a protobuf
    It could be a public address, a gift code, or a payment request"""
    checksum_and_wrapper_bytes = base58.b58decode(b58_string)
    wrapper_bytes = checksum_and_wrapper_bytes[4:]
    if add_checksum_and_b58(wrapper_bytes) != b58_string:
        return None
    wrapper = printable_pb2.PrintableWrapper()
    wrapper.ParseFromString(wrapper_bytes)
    return wrapper


def b58_wrapper_to_b64_public_address(b58_string: str) -> str:
    """Convert a b58-encoded PrintableWrapper address into a b64-encoded PublicAddress protobuf"""
    wrapper = b58_wrapper_to_protobuf(b58_string)
    if wrapper:
        public_address = wrapper.public_address
        public_address_bytes = public_address.SerializeToString()
        return base64.b64encode(public_address_bytes).decode("utf-8")
    return None


def add_checksum_and_b58(wrapper_bytes: bytes) -> str:
    new_checksum = zlib.crc32(wrapper_bytes)
    new_checksum_bytes = new_checksum.to_bytes(4, byteorder="little")
    return base58.b58encode(new_checksum_bytes + wrapper_bytes).decode()


# def b58_string_is_public_address(b58_string):
#     """Check if a b58-encoded string contains a PrintableWrapper protobuf with a PublicAddress"""
#     if not b58_string_passes_checksum(b58_string):
#         return False

#     checksum_and_wrapper_bytes = base58.b58decode(b58_string)
#     wrapper_bytes = checksum_and_wrapper_bytes[4:]
#     wrapper = printable_pb2.PrintableWrapper()

#     try:
#         wrapper.ParseFromString(wrapper_bytes)
#         return wrapper.PublicAddress is not None
#     except Exception:
#         return False


def b64_receipt_to_full_service_receipt(b64_string: str) -> dict:
    """Convert a b64-encoded protobuf Receipt into a full-service receipt object"""
    receipt_bytes = base64.b64decode(b64_string)
    receipt = external_pb2.Receipt.FromString(receipt_bytes)

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


def full_service_receipt_to_b64_receipt(full_service_receipt: dict) -> str:
    """Convert a full-service receipt object to a b64-encoded protobuf Receipt"""
    assert full_service_receipt["object"] == "receiver_receipt"

    public_key = external_pb2.CompressedRistretto.FromString(
        bytes.fromhex(full_service_receipt["public_key"])
    )
    confirmation = external_pb2.TxOutConfirmationNumber.FromString(
        bytes.fromhex(full_service_receipt["confirmation"])
    )
    tombstone_block = int(full_service_receipt["tombstone_block"])
    amount_commitment = external_pb2.CompressedRistretto(
        data=bytes.fromhex(full_service_receipt["amount"]["commitment"])
    )
    amount_masked_value = int(full_service_receipt["amount"]["masked_value"])
    amount = external_pb2.Amount(
        commitment=amount_commitment, masked_value=amount_masked_value
    )
    r = external_pb2.Receipt(
        public_key=public_key,
        confirmation=confirmation,
        tombstone_block=tombstone_block,
        amount=amount,
    )
    return base64.b64encode(r.SerializeToString()).decode("utf-8")
