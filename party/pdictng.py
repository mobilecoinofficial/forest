import asyncio
import concurrent.futures
import gzip
import hashlib
import json
import os
import time
from typing import Union, Any

import aiohttp
import base58
from Crypto.Cipher import AES

NAMESPACE = os.getenv("FLY_APP_NAME") or open("/etc/hostname").read().strip()
SALT = os.getenv("SALT", "ECmG8HtNNMWb4o2bzyMqCmPA6KTYJPCkd")
# build your AESKEY envvar with this: cat /dev/urandom | head -c 32 | base58
AESKEY = base58.b58decode(os.getenv("AESKEY", "kWKuomB9Ty3GcJ9yA1yED").encode()) * 2

if not AESKEY or len(AESKEY) not in [16, 32]:
    raise ValueError(
        "Need to set 128b or 256b (16 or 32 byte) AESKEY envvar for persistence. It should be base58 encoded."
    )

pAUTH = os.getenv("PAUTH")

if not pAUTH:
    raise ValueError("Need to set PAUTH envvar for persistence")


def encrypt(data: bytes, key: bytes) -> bytes:
    """Accepts data (as arbitrary length bytearray) and key (as 16B or 32B bytearray) and returns authenticated and encrypted blob (as bytearray)"""
    cipher = AES.new(key, AES.MODE_EAX)
    ciphertext, authtag = cipher.encrypt_and_digest(data)
    return cipher.nonce + authtag + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    """Accepts ciphertext (as arbitrary length bytearray) and key (as 16B or 32B bytearray) and returns decrypted (plaintext) blob (as bytearray)"""
    cipher = AES.new(key, AES.MODE_EAX, data[:16])
    return cipher.decrypt_and_verify(data[32:], data[16:32])


def get_safe_key(key_: str) -> str:
    """returns a base58 encoded sha256sum of a salted key"""
    return base58.b58encode(hashlib.sha256(f"{SALT}{key_}".encode()).digest()).decode()


def get_safe_value(value_: str) -> str:
    """returns a base58 encoded aes128 AES EAX mode encrypted gzip compressed value"""
    if isinstance(value_, str):
        value_ = value_.encode()
    return base58.b58encode(encrypt(gzip.compress(value_), AESKEY)).decode()


def get_cleartext_value(value_: str) -> str:
    """decrypts, decodes, decompresses a b58 blob returning cleartext"""
    return gzip.decompress(decrypt(base58.b58decode(value_), AESKEY)).decode()


class pKVStoreClient:
    """Strongly consistent, persistent storage.
    On top of Postgresql and Postgrest.
    """

    def __init__(
        self,
        base_url: str = "https://vwaurvyhomqleagryqcc.supabase.co/rest/v1/keyvalue",
        auth_str: str = pAUTH,
        namespace: str = NAMESPACE,
    ):
        self.url = base_url
        self.conn = aiohttp.ClientSession()
        self.auth = auth_str
        self.namespace = get_safe_key(namespace)

    async def post(self, key: str, data: str, ttl_seconds: int = 600) -> str:
        key = get_safe_key(key)
        data = get_safe_value(data)
        kv_set_req = self.conn.post(
            f"{self.url}",
            headers={
                "Content-Type": "application/json",
                "apikey": f"{self.auth}",
                "Authorization": f"Bearer {self.auth}",
                "Prefer": "return=representation",
            },
            data=json.dumps(
                dict(
                    key_=key,
                    value=data,
                    created_at=time.time(),
                    namespace=self.namespace,
                )
            ),
        )
        # try to set
        async with kv_set_req as resp:
            resp_text = await resp.text()
            # if set fails
            if "duplicate key value violates unique constraint" in resp_text:
                # do update (patch not post)
                async with self.conn.patch(
                    f"{self.url}?key_=eq.{key}&namespace=eq.{self.namespace}",
                    headers={
                        "Content-Type": "application/json",
                        "apikey": f"{self.auth}",
                        "Authorization": f"Bearer {self.auth}",
                        "Prefer": "return=representation",
                    },
                    data=json.dumps(
                        dict(
                            value=data,
                            updated_at=time.time(),
                            namespace=self.namespace,
                        )
                    ),
                ) as resp:
                    return await resp.json()
            return json.loads(resp_text)

    async def get(self, key: str) -> str:
        """Get and return value of an object with the specified key and namespace"""
        key = get_safe_key(key)
        kv_get_req = self.conn.get(
            f"{self.url}?select=value&key_=eq.{key}&namespace=eq.{self.namespace}",
            headers={
                "Accept": "application/octet-stream",
                "apikey": f"{self.auth}",
                "Authorization": f"Bearer {self.auth}",
            },
        )
        async with kv_get_req as resp:
            maybe_res = await resp.text()
            if maybe_res:
                return get_cleartext_value(maybe_res)
            return ""


class fastpKVStoreClient:
    """Strongly consistent, persistent storage.
    Stores a sneak table mapping keys to their existence to update faster.
    On top of Postgresql and Postgrest.
    Schema:
                                         Table "public.keyvalue"
       Column   |       Type       | Collation | Nullable |             Default
    ------------+------------------+-----------+----------+----------------------------------
     key        | bigint           |           | not null | generated by default as identity
     value      | text             |           |          | 'EMPTY'::text
     update     | text             |           |          |
     namespace  | text             |           |          |
     key_       | text             |           | not null |
     created_at | double precision |           |          |
     ttl        | bigint           |           |          |
     updated_at | double precision |           |          |
    Indexes:
        "keyvalue_pkey" PRIMARY KEY, btree (key)
        "keyvalue_key__key" UNIQUE CONSTRAINT, btree (key_)

    """

    def __init__(
        self,
        base_url: str = "https://vwaurvyhomqleagryqcc.supabase.co/rest/v1/keyvalue",
        auth_str: str = pAUTH,
        namespace: str = NAMESPACE,
    ):
        self.url = base_url
        self.conn = aiohttp.ClientSession()
        self.auth = auth_str
        self.namespace = get_safe_key(namespace)
        self.exists = {}
        self.headers = {
            "Content-Type": "application/json",
            "apikey": f"{self.auth}",
            "Authorization": f"Bearer {self.auth}",
            "Prefer": "return=representation",
        }

    async def post(self, key: str, data: str, ttl_seconds: int = 600) -> str:
        key = get_safe_key(key)
        data = get_safe_value(data)
        # try to set
        if self.exists.get(key):
            async with self.conn.patch(
                f"{self.url}?key_=eq.{key}&namespace=eq.{self.namespace}",
                headers=self.headers,
                data=json.dumps(
                    dict(
                        value=data,
                        updated_at=time.time(),
                        namespace=self.namespace,
                    )
                ),
            ) as resp:
                return await resp.json()
        async with self.conn.post(
            f"{self.url}",
            headers=self.headers,
            data=json.dumps(
                dict(
                    key_=key,
                    value=data,
                    created_at=time.time(),
                    namespace=self.namespace,
                )
            ),
        ) as resp:
            resp_text = await resp.text()
            # if set fails
            if "duplicate key value violates unique constraint" in resp_text:
                self.exists[key] = True
                # do update (patch not post)
                async with self.conn.patch(
                    f"{self.url}?key_=eq.{key}&namespace=eq.{self.namespace}",
                    headers=self.headers,
                    data=json.dumps(
                        dict(
                            value=data,
                            updated_at=time.time(),
                            namespace=self.namespace,
                        )
                    ),
                ) as resp:
                    return await resp.json()
            return json.loads(resp_text)

    async def get(self, key: str) -> str:
        """Get and return value of an object with the specified key and namespace"""
        key = get_safe_key(key)
        async with self.conn.get(
            f"{self.url}?select=value&key_=eq.{key}&namespace=eq.{self.namespace}",
            headers={
                "Accept": "application/octet-stream",
                "apikey": f"{self.auth}",
                "Authorization": f"Bearer {self.auth}",
            },
        ) as resp:
            maybe_res = await resp.text()
            if maybe_res:
                self.exists[key] = True
                return get_cleartext_value(maybe_res)
            return ""


class aPersistDict:
    """Async, consistent, persistent storage.
    Does not inherit from dict, but behaves mostly in the same way.
    Care is taken to offer asynchronous methods and strong consistency.
    This can be used for
        - inventory
        - subscribers
        - config info
    in a way that are persisted across reboots.
    No schemas and privacy preserving, but could be faster.
    Each write takes about 70 ms.
    """

    def __init__(self, *args, **kwargs) -> None:
        """If an argument is provided or a 'tag' keyword argument is passed...
        this will be used as a tag for backup / restore.
        """
        self.tag = ""
        if args:
            self.tag = args[0]
        if "tag" in kwargs:
            self.tag = kwargs.pop("tag")
        self.dict_ = {}
        self.client = fastpKVStoreClient()
        self.rwlock = asyncio.Lock()
        self.loop = asyncio.get_event_loop()
        self.init_task = asyncio.create_task(self.finish_init(**kwargs))
        self.write_task = None

    def __repr__(self) -> str:
        return f"a{self.dict_}"

    def __str__(self) -> str:
        return f"a{self.dict_}"

    async def __getitem__(self, key) -> Any:
        return await self.get(key)

    def __setitem__(self, key: str, value: Union[float, str]):
        if self.write_task and not self.write_task.done():
            raise ValueError("Can't set value. write_task incomplete.")
        self.write_task = asyncio.create_task(self.set(key, value))

    async def finish_init(self, **kwargs):
        async with self.rwlock:
            key = f"Persist_{self.tag}_{NAMESPACE}"
            result = await self.client.get(key)
            if result:
                self.dict_ = json.loads(result)
            self.dict_.update(**kwargs)

    async def get(self, key, default=None):
        # always wait for pending writes - where a task has been created but lock not held
        if self.write_task:
            await self.write_task
            self.write_task = None
        # then grab the lock
        async with self.rwlock:
            return self.dict_.get(key, default)

    async def set(self, key, value):
        async with self.rwlock:
            self.dict_.update({key: value})
            key = f"Persist_{self.tag}_{NAMESPACE}"
            value = json.dumps(self.dict_)
            val = await self.client.post(key, value)
            return val


class PersistDict(dict):
    """Consistent, persistent storage.
    A Python dict that synchronously backs up its contents to Postgresql.
    Care is taken to do this in a threadsafe way, ie using concurrent.futures.ThreadPoolExec -
        such that this backup operation does not block the event loop or thread that invokes it.
    Writes are heavy - this should not be used for a persistent cache (frequently written) - but can be used for storing...
        - inventory
        - subscribers
        - config info
    in a way that are persisted across reboots.
    No schemas and privacy preserving, but could be faster.
    Each write takes aboout 90ms.
    """

    def __init__(self, *args, **kwargs):
        """If an argument is provided or a 'tag' keyword argument is passed...
        this will be used as a tag for backup / restore.
        """
        self.tag = ""
        if args:
            self.tag = args[0]
        if "tag" in kwargs:
            self.tag = kwargs.pop("tag")
        self.thread_pool = concurrent.futures.ThreadPoolExecutor()

        async def async_get():
            key = f"Persist_{self.tag}_{NAMESPACE}"
            client = pKVStoreClient()
            val = await client.get(key)
            await client.conn.close()
            return val

        # creates a new event loop on a separate thread, invokes async_get function, returns result
        result = self.thread_pool.submit(asyncio.run, async_get()).result()
        dict_ = {}
        if result:
            dict_ = json.loads(result)
        dict_.update(**kwargs)
        super().__init__(**dict_)

    def __getitem__(self, key):
        return super().__getitem__(key)

    def save_state(self):
        """JSON serialize and update self."""
        jsond = json.dumps(self)

        async def async_push():
            key = f"Persist_{self.tag}_{NAMESPACE}"
            value = jsond
            client = pKVStoreClient()
            val = await client.post(key, value)
            await client.conn.close()
            return val

        return self.thread_pool.submit(asyncio.run, async_push()).result()

    def __setitem__(self, key: str, value: Union[float, str]):
        super().__setitem__(key, value)
        return self.save_state()
