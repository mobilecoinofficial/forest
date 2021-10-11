from typing import Union
from decimal import Decimal
import base64
import mobilecoin
import base58

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


def b64_receipt_to_full_service_receipt(b64_string: str) -> dict:
    """Convert a b64-encoded protobuf Receipt into a full-service receipt object"""
    receipt_bytes = base64.b64decode(b64_string)
    receipt = Mobilecoin.Receipt.FromString(receipt_bytes)  # type: ignore # pylint: disable=no-member

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
