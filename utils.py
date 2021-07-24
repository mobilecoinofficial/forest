import asyncio
import logging
import os
import sys
from asyncio.subprocess import PIPE, create_subprocess_exec
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, cast

import aiohttp
import phonenumbers as pn
from aiohttp import web

HOSTNAME = open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID
APP_NAME = os.getenv("FLY_APP_NAME")
URL = f"https://{APP_NAME}.fly.dev"
LOCAL = APP_NAME is None
ROOT_DIR = "/tmp/local-signal" if LOCAL else "/app"


def FuckAiohttp(record: logging.LogRecord) -> bool:
    str_msg = str(getattr(record, "msg", ""))
    if "was destroyed but it is pending" in str_msg:
        return False
    if str_msg.startswith("task:") and str_msg.endswith(">"):
        return False
    return True


logging.basicConfig(
    level=logging.DEBUG,
    format="{levelname} {module}:{lineno}: {message}",
    style="{",
)
logger = logging.getLogger()
logging.getLogger().handlers[0].addFilter(FuckAiohttp)


def load_secrets(env: Optional[str] = None) -> None:
    if not env:
        env = os.environ.get("ENV", "dev")
    try:
        logging.info("loading secrets from %s_secrets", env)
        secrets = [line.strip().split("=", 1) for line in open(f"{env}_secrets")]
        can_be_a_dict = cast(list[tuple[str, str]], secrets)
        os.environ.update(dict(can_be_a_dict))
    except FileNotFoundError:
        pass


def get_secret(key: str, env: Optional[str] = None) -> str:
    try:
        return os.environ[key]
    except KeyError:
        load_secrets(env)
        return os.environ.get(key) or ""  # fixme


def teli_format(raw_number: str) -> str:
    return str(pn.parse(raw_number, "US").national_number)


def signal_format(raw_number: str) -> str:
    return pn.format_number(pn.parse(raw_number, "US"), pn.PhoneNumberFormat.E164)


@asynccontextmanager
async def get_url(port: int = 8080) -> AsyncIterator[str]:
    if not APP_NAME:
        try:
            logging.info("starting tunnel")
            tunnel = await create_subprocess_exec(
                *(f"lt -p {port}".split()),
                stdout=PIPE,
            )
            assert tunnel.stdout
            line = await tunnel.stdout.readline()
            url = line.decode().lstrip("your url is: ").strip()
            yield url + "/inbound"
        finally:
            logging.info("terminaitng tunnel")
            tunnel.terminate()
    else:
        yield APP_NAME + ".fly.io"


class ReceiveSMS:
    def __init__(self, port: int = 8080) -> None:
        self.msgs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.port = port

    async def handle_sms(self, request: web.Request) -> web.Response:
        # A coroutine that reads POST parameters from request body.
        # Returns MultiDictProxy instance filled with parsed data.
        msg_obj = dict(await request.post())
        logging.info("ReceiveSMS.handle_sms got %s", msg_obj)
        await self.msgs.put(msg_obj)
        return web.json_response({"status": "OK"})

    @asynccontextmanager
    async def receive(self) -> AsyncIterator[web.TCPSite]:
        self.app = web.Application()
        routes = [web.post("/inbound", self.handle_sms)]
        self.app.add_routes(routes)
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        logging.info("starting ReceiveSMS")
        try:
            await site.start()
            yield site
        finally:
            logging.info("shutting down ReceiveSMS")
            # try:
            await self.app.shutdown()
            await self.app.cleanup()
            # except (OSError, RuntimeError): pass


class Teli:
    def __init__(self) -> None:
        self.session = aiohttp.client.ClientSession()

    async def set_sms_url(self, raw_number: str, url: str) -> dict:
        number = teli_format(raw_number)
        async with self.session.get(
            "https://apiv1.teleapi.net/user/dids/get",
            params={
                "token": get_secret("TELI_KEY"),
                "number": number,
            },
        ) as resp:
            did_lookup = await resp.json()
        if did_lookup.get("status") == "error":
            logging.error(did_lookup)
            return did_lookup  # not sure about this
        logging.info("did lookup: %s", did_lookup)
        did_id = did_lookup.get("data", {}).get("id")
        params = {
            "token": get_secret("TELI_KEY"),
            "did_id": did_id,
            "url": url,
        }
        logging.info(url)
        async with self.session.get(
            "https://apiv1.teleapi.net/user/dids/smsurl/set", params=params
        ) as resp:
            set_url = await resp.json()
        logging.info(set_url)
        async with self.session.get(
            "https://apiv1.teleapi.net/user/dids/get",
            params={
                "token": get_secret("TELI_KEY"),
                "number": number,
            },
        ) as resp:
            actual_url = (await resp.json())["data"]["sms_post_url"]
        logging.info(actual_url)
        return set_url

    async def list_our_numbers(
        self,
    ) -> list[str]:
        async with self.session.get(
            "https://apiv1.teleapi.net/user/dids/list",
            params={"token": get_secret("TELI_KEY")},
        ) as resp:
            blob = await resp.json()
        return blob

    async def search_numbers(
        self,
        area_code: Optional[str] = None,
        nxx: Optional[str] = None,
        search_term: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[str]:
        """
        search teli for available numbers to buy. nxx is middle three digits
        for search_term 720***test will search for 720***8378
        if you don't specify anything, teli will probably not respond
        """
        params = {
            "token": get_secret("TELI_KEY"),
            "npa": area_code,
            "nxx": nxx,
            "search": search_term,
            "limit": limit,
        }
        # this is a little ugly
        nonnull_params = {key: value for key, value in params.items() if value}
        async with self.session.get(
            "https://apiv1.teleapi.net/dids/list", params=nonnull_params
        ) as resp:
            blob = await resp.json()
        if "error" in blob:
            logging.warning(blob)
        dids = blob["data"]["dids"]
        return [info["number"] for info in dids]

    async def buy_number(
        self, number: str, sms_post_url: Optional[str] = None
    ) -> dict:
        params = {
            "token": get_secret("TELI_KEY"),
            "number": number,
            "sms_post_url": sms_post_url,
        }
        nonnull_params = {key: value for key, value in params.items() if value}
        logging.info("buying %s", number)
        async with self.session.get(
            "https://apiv1.teleapi.net/dids/order", params=nonnull_params
        ) as resp:
            logging.info(resp)
            return await resp.json()


async def print_sms(raw_number: str, port: int = 8080) -> None:
    logging.info(port)
    receiver = ReceiveSMS()
    async with get_url(port) as url, receiver.receive():
        await Teli().set_sms_url(raw_number, url)
        try:
            while 1:
                print(await receiver.msgs.get())
            await asyncio.sleep(10 ** 9)
        except KeyboardInterrupt:
            return
    return


if __name__ == "__main__":
    try:
        num = sys.argv[1]
        assert teli_format(num) == num
        asyncio.run(print_sms(num))
    except (IndexError, AssertionError):
        pass
