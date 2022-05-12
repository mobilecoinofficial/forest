# Copyright 2022 MobileCoin Inc
# Copyright 2022 Ilia Daniher <i@mobilecoin.com>
# MIT LICENSE

import asyncio
import concurrent.futures
import json
import os
import time
from typing import Union, Any

import aiohttp

NAMESPACE = os.getenv("FLY_APP_NAME") or open("/etc/hostname").read().strip()
SALT = os.getenv("SALT", "ECmG8HtNNMWb4o2bzyMqCmPA6KTYJPCkd")
AUTH = os.getenv("XAUTH", "totallyAuthorized")
pAUTH = os.getenv("PAUTH", "")


class KVStoreClient:
    """Eventually consistent, highly available, persistent storage.
    On top of Cloudflare KV Workers.
    """

    def __init__(
        self,
        base_url: str = "https://kv.sometimes.workers.dev",
        auth_str: str = AUTH,
    ) -> None:
        self.url = base_url
        self.conn = aiohttp.ClientSession()
        self.auth = auth_str

    async def post(self, key: str, data: str, ttl_seconds: int = 600) -> str:
        kv_set_req = self.conn.post(
            f"{self.url}/{key}?ttl={ttl_seconds}&value={data}",
            headers={
                "Content-Type": "text/plain; charset=utf8",
                "X-AUTH": f"{self.auth}",
            },
        )
        async with kv_set_req as resp:
            return await resp.text()

    async def get(self, key: str) -> str:
        kv_get_req = self.conn.get(f"{self.url}/{key}")
        async with kv_get_req as resp:
            return await resp.text()


class pKVStoreClient:
    """Strongly consistent, persistent storage.
    On top of Postgresql and Postgrest.
    """

    def __init__(
        self,
        base_url: str = "https://vwaurvyhomqleagryqcc.supabase.co/rest/v1/keyvalue",
        auth_str: str = pAUTH,
        namespace: str = NAMESPACE,
    ) -> None:
        self.url = base_url
        self.conn = aiohttp.ClientSession()
        self.auth = auth_str
        self.namespace = namespace

    async def post(self, key: str, data: str) -> dict[Any, Any]:
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
        kv_get_req = self.conn.get(
            f"{self.url}?select=value&key_=eq.{key}&namespace=eq.{self.namespace}",
            headers={
                "Accept": "application/octet-stream",
                "apikey": f"{self.auth}",
                "Authorization": f"Bearer {self.auth}",
            },
        )
        async with kv_get_req as resp:
            return await resp.text()


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
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """If an argument is provided or a 'tag' keyword argument is passed...
        this will be used as a tag for backup / restore.
        """
        self.tag = ""
        if args:
            self.tag = args[0]
        if "tag" in kwargs:
            self.tag = kwargs.pop("tag")
        self.thread_pool = concurrent.futures.ThreadPoolExecutor()

        async def async_get() -> str:
            key = f"Persist_{self.tag}_{NAMESPACE}"
            client = pKVStoreClient()
            val = await client.get(key)
            await client.conn.close()
            return val

        # creates a new event loop on a separate thread, invokes async_get function, returns result
        result = self.thread_pool.submit(asyncio.run, async_get()).result()  # type: ignore
        dict_ = {}
        if isinstance(result, str) and result:
            dict_ = json.loads(result)
        dict_.update(**kwargs)
        super().__init__(**dict_)

    def save_state(self) -> Any:
        """JSON serialize and update self."""
        jsond = json.dumps(self)

        async def async_push() -> dict[Any, Any]:
            key = f"Persist_{self.tag}_{NAMESPACE}"
            value = jsond
            client = pKVStoreClient()
            val = await client.post(key, value)
            await client.conn.close()
            return val

        return self.thread_pool.submit(asyncio.run, async_push()).result()  # type: ignore

    def __setitem__(self, key: str, value: Union[float, str]) -> None:
        super().__setitem__(key, value)
        self.save_state()
