"""
remember, teli uses national_number; signal uses E164
"""
from typing import Any, Callable, Coroutine, Optional, AsyncIterator, cast
from contextlib import asynccontextmanager
from asyncio.subprocess import create_subprocess_exec, PIPE
import asyncio
import logging
import os
import sys
import requests
import phonenumbers as pn
from aiohttp import web

HOSTNAME = open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID
APP_NAME = os.getenv("FLY_APP_NAME")
URL = f"https://{APP_NAME}.fly.dev"
LOCAL = APP_NAME is None
ROOT_DIR = "/tmp/local-signal" if LOCAL else "/app"

logging.basicConfig(
    level=logging.DEBUG, format="{levelname} {module}:{lineno}: {message}", style="{"
)




def load_secrets(env: Optional[str] = None) -> None:
    if not env:
        env = "dev"
    secrets = [line.strip().split("=", 1) for line in open(f"{env}_secrets")]
    can_be_a_dict = cast(list[tuple[str, str]], secrets)
    os.environ.update(dict(can_be_a_dict))


def get_secret(key: str, env: Optional[str] = None) -> str:
    try:
        return os.environ[key]
    except KeyError:
        load_secrets(env)
        return os.environ.get(key) or "" # fixme

def teli_format(raw_number: str) -> str:
    return str(pn.parse(raw_number, "US").national_number)


def signal_format(raw_number: str) -> str:
    return pn.format_number(
        pn.parse(raw_number, "US"), pn.PhoneNumberFormat.E164
    )

@asynccontextmanager
async def get_url(port: int = 8080) -> AsyncIterator[str]:
    if not APP_NAME:
        try:
            print("starting tunnel")
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


Callback = Callable[[dict], Coroutine[Any, Any, None]]


class ReceiveSMS:
    def __init__(self, port: int = 8080
    ) -> None:
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
            #try:
            await self.app.shutdown()
            await self.app.cleanup()
            #except (OSError, RuntimeError): pass

async def aprint(msg: Any) -> None:
    print(msg)


# @asynccontextmanager
# async def receive_sms(
#     callback: Callback = aprint, port: int = 8080
# ) -> AsyncIterator[web.TCPSite]:
#     print(port)

#     async def handle_sms(request: web.Request) -> web.Response:
#         msg_obj = dict(await request.post())
#         logging.info(msg_obj)
#         await callback(msg_obj)
#         return web.json_response({"status": "OK"})

#     app = web.Application()
#     app.add_routes([web.post("/inbound", handle_sms)])
#     runner = web.AppRunner(app)
#     await runner.setup()
#     print(port)
#     site = web.TCPSite(runner, "0.0.0.0", port)
#     logging.info("starting SMS receiving server")
#     try:
#         await site.start()
#         yield site
#     finally:
#         logging.info("shutting down SMS server")
#         await app.shutdown()
#         await app.cleanup()
#         await site.stop()


def set_sms_url(raw_number: str, url: str) -> dict:
    number = teli_format(raw_number)
    did_lookup = requests.get(
        "https://apiv1.teleapi.net/user/dids/get",
        params={
            "token": get_secret("TELI_KEY"),
            "number": number,
        },
    ).json()
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
    set_url = requests.get(
        "https://apiv1.teleapi.net/user/dids/smsurl/set", params=params
    ).json()
    logging.info(set_url)
    actual_url = requests.get(
        "https://apiv1.teleapi.net/user/dids/get",
        params={
            "token": get_secret("TELI_KEY"),
            "number": number,
        },
    ).json()["data"]["sms_post_url"]
    logging.info(actual_url)
    return set_url


async def print_sms(raw_number: str, port: int = 8080) -> None:
    print(port)
    receiver = ReceiveSMS()
    async with get_url(port) as url, receiver.receive():
        set_sms_url(raw_number, url)
        try:
            while 1:
                print(await receiver.msgs.get())
            await asyncio.sleep(10 ** 9)
        except KeyboardInterrupt:
            return
    return


def list_our_numbers() -> list[str]:
    blob = requests.get(
        "https://apiv1.teleapi.net/user/dids/list",
        params={"token": get_secret("TELI_KEY")},
    ).json()
    # this actually needs to figure out the url of the other environment
    # so prod doesn't take dev numbers
    def predicate(did: dict[str, str]) -> bool:
        # this actually needs to check if it's the *other* env
        # and properly if the number is already used in another way...
        # maybe based on whether the number is on signal...
        url = did["sms_post_url"]
        return "loca.lt" in url or "trees-dev" in url

    return [did["number"] for did in blob["data"] if predicate(did)]


def search_numbers(
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
    blob = requests.get(
        "https://apiv1.teleapi.net/dids/list", params=nonnull_params
    ).json()
    if "error" in blob:
        logging.warning(blob)
    dids = blob["data"]["dids"]
    return [info["number"] for info in dids]


def buy_number(number: str, sms_post_url: Optional[str] = None) -> dict:
    params = {
        "token": get_secret("TELI_KEY"),
        "number": number,
        "sms_post_url": sms_post_url,
    }
    nonnull_params = {key: value for key, value in params.items() if value}
    logging.info("buying %s", number)
    resp = requests.get(
        "https://apiv1.teleapi.net/dids/order", params=nonnull_params
    )
    logging.info(resp)
    logging.info(resp.text)
    return resp.json()


def get_signal_captcha(buy: Optional[bool] = None) -> Optional[str]:
    try:
        solution = open("/tmp/captcha").read().lstrip("signalcaptcha://")
        logging.info("using local captcha")
        os.rename("/tmp/captcha", "/tmp/used_captcha")
        return solution
    except FileNotFoundError:
        if buy is None:
            input("press enter if you've put a captcha in /tmp/captcha or want to buy one")
            return get_signal_captcha(True)
        if buy is False:
            return None
    logging.info("buying a captcha...")
    try:
        blob = requests.post(
            get_secret("SOLVER"),
            data="https://signalcaptchas.org/registration/generate.html",
        ).json()
        solution = blob.get("solution", {}).get("gRecaptchaResponse")
    except requests.exceptions.RequestException as e:
        logging.error(e)
        if input("type something if you've put a new captcha in /tmp/captcha"):
            return get_signal_captcha()
    logging.info("captcha solution: %s", solution)
    return solution


if __name__ == "__main__":
    try:
        num = sys.argv[1]
        assert teli_format(num) == num
        asyncio.run(print_sms(num))
    except (IndexError, AssertionError):
        pass
