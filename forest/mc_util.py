from typing import Union
from decimal import Decimal
import base64
import mobilecoin

# adapted from https://github.com/mobilecoinofficial/full-service/tree/main/python-utils/mc_util

PMOB = Decimal("1e12")

Num = Union[Decimal, float, int]


def mob2pmob(x: Num) -> int:
    """Convert from MOB to picoMOB."""
    return round(Decimal(x) * PMOB)


def pmob2mob(x: Num) -> float:
    """Convert from picoMOB to MOB."""
    result = int(x) / PMOB
    return float(result)


# def b64_public_address_to_b58_wrapper(b64_string):
#     """Convert a b64-encoded PublicAddress protobuf to a b58-encoded PrintableWrapper protobuf"""
#     public_address_bytes = base64.b64decode(b64_string)

#     public_address = external_pb2.PublicAddress()
#     public_address.ParseFromString(public_address_bytes)

#     wrapper = printable_pb2.PrintableWrapper()
#     wrapper.public_address.CopyFrom(public_address)

#     wrapper_bytes = wrapper.SerializeToString()

#     checksum = zlib.crc32(wrapper_bytes)
#     checksum_bytes = checksum.to_bytes(4, byteorder="little")

#     checksum_and_wrapper_bytes = checksum_bytes + wrapper_bytes

#     return base58.b58encode(checksum_and_wrapper_bytes).decode("utf-8")


def b64_receipt_to_full_service_receipt(b64_string: str) -> dict:
    """Convert a b64-encoded protobuf Receipt into a full-service receipt object"""
    receipt_bytes = base64.b64decode(b64_string)
    receipt = mobilecoin.Receipt.FromString(receipt_bytes)  # type: ignore # pylint: disable=no-member

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
