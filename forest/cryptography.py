import gzip
import hashlib
import logging
import os
from typing import Union, cast

import base58
from Crypto.Cipher import AES, _mode_eax

SALT = os.getenv("SALT", "ECmG8HtNNMWb4o2bzyMqCmPA6KTYJPCkd")
# build your AESKEY envvar with this: cat /dev/urandom | head -c 32 | base58
AESKEY = base58.b58decode(os.getenv("AESKEY", "kWKuomB9Ty3GcJ9yA1yED").encode()) * 2

if not AESKEY or len(AESKEY) not in [16, 32, 64]:
    logging.error(
        "Need to set 128b or 256b (16 or 32 byte) AESKEY envvar for persistence. It should be base58 encoded."
    )

if len(AESKEY) == 64:
    AESKEY = AESKEY[:32]


def encrypt(data: bytes, key: bytes) -> bytes:
    """Accepts data (as arbitrary length bytearray) and key (as 16B or 32B bytearray) and returns authenticated and encrypted blob (as bytearray)"""
    cipher = cast(_mode_eax.EaxMode, AES.new(key, AES.MODE_EAX))
    ciphertext, authtag = cipher.encrypt_and_digest(data)  # pylint: disable
    return cipher.nonce + authtag + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    """Accepts ciphertext (as arbitrary length bytearray) and key (as 16B or 32B bytearray) and returns decrypted (plaintext) blob (as bytearray)"""
    cipher = cast(_mode_eax.EaxMode, AES.new(key, AES.MODE_EAX, data[:16]))
    return cipher.decrypt_and_verify(data[32:], data[16:32])  # pylint: disable


def hash_salt(key_: str, salt: str = SALT) -> str:
    """returns a base58 encoded sha256sum of a salted key"""
    return base58.b58encode(hashlib.sha256(f"{salt}{key_}".encode()).digest()).decode()


def get_ciphertext_value(value_: Union[str, bytes]) -> str:
    """returns a base58 encoded aes128 AES EAX mode encrypted gzip compressed value"""
    if isinstance(value_, str):
        value_bytes = value_.encode()
    elif isinstance(value_, bytes):
        value_bytes = value_
    else:
        raise ValueError
    return base58.b58encode(encrypt(gzip.compress(value_bytes), AESKEY)).decode()


def get_cleartext_value(value_: str) -> str:
    """decrypts, decodes, decompresses a b58 blob returning cleartext"""
    return gzip.decompress(decrypt(base58.b58decode(value_), AESKEY)).decode()
