#!/usr/bin/python3.9
import asyncio
import os
import aioprocessing
import aiohttp
from aiohttp import web
import sys
import shutil
import re
import json
import urllib
import urllib.parse
from requests import get
import main
from datastore import SignalDatastore, get_account_interface
# avaiable options: state, npa (area code), nxx(midlde three digits), search (720***test will search for 720***8378)
# number (string, required)
# sms_post_url (string, optional)

# load secrets file into environment before launching main
if os.path.exists("dev_secrets"):
    secrets = dict(line.strip().split("=", 1) for line in open("dev_secrets"))
    os.environ.update(secrets)


TELI_KEY = os.environ.get("TELI_KEY")
if not TELI_KEY:
    raise ValueError("Missing Teli Key")


def buy_number() -> str:
    blob = get(
        f"https://apiv1.teleapi.net/dids/list?token={TELI_KEY}&nxx=617&limit=1"
    ).json()
    if "error" in blob:
        print(blob)
    dids = blob["data"]["dids"]
    available_numbers = [info["number"] for info in dids]
    new_number = available_numbers[0]
    if input(f"buy {new_number}? ") != "yes":
        print("not buying number")
        sys.exit(0)
    # buy {new_number}? y/n (response/react)
    url = f"https://apiv1.teleapi.net/dids/order?token={TELI_KEY}&number={available_numbers[0]}"
    print(url)
    resp = get(url)
    print(resp.text)
    print(resp)
    open("numbers", "w").write(", ".join(numbers + [new_number]))
    return new_number


# https://apidocs.teleapi.net/api/my-phone-numbers/set-call-forwarding
#
# https://apiv1.teleapi.net/user/dids/smsurl/set

# token (string, required)
# did_id (int, required)
# url (string, required)


async def inbound_handler(request):
    # A coroutine that reads POST parameters from request body.
    # Returns MultiDictProxy instance filled with parsed data.
    # If method is not POST, PUT, PATCH, TRACE or DELETE or content_type is not empty or application/x-www-form-urlencoded or multipart/form-data returns empty multidict.
    msg_obj = dict(await request.post()) 
    await request.app["sms_queue"].put(msg_obj)
    print(msg_obj)
    return web.json_response({"status": "OK"})


app = web.Application()

app.add_routes(
    [
        web.post("/inbound", inbound_handler),
    ]
)


async def start_app():
    app["sms_queue"] = asyncio.Queue()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 8080)
    print("starting SMS receiving server")

    await site.start()


async def set_pingback(target) -> asyncio.subprocess.Process:
    did_lookup = await (
        await client_session.get(
            f"https://apiv1.teleapi.net/user/dids/get?token={TELI_KEY}&number={target}"
        )
    ).json()
    print(did_lookup)
    did_id = did_lookup.get("data").get("id")
    # if retcode==127: exec("sudo npm install -g localtunnel")
    tunnel = await asyncio.subprocess.create_subprocess_exec(
        *("lt -p 8080".split()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    verif_url = (await tunnel.stdout.readline()).decode().strip(
        "your url is: "
    ).strip() + "/inbound"
    print(verif_url)
    set_req = await client_session.get(
        f"https://apiv1.teleapi.net/user/dids/smsurl/set?token={TELI_KEY}&did_id={did_id}&url={verif_url}"
    )
    print(await set_req.text())
    return tunnel

async def local_main():
    #    await main.start_sessions(globals())
    client_session = aiohttp.ClientSession()
    account_interface = get_account_interface()
    await start_app()



    # this all needs to be adjusted for the tarball
    # def is_unregistered(number):
    #     if len(sys.argv) > 1 and sys.argv[1] == "reregister":
    #         return True
    #     account = await account_interface.get_user(f"1{number}"))
    #     return not user or not json.loads(account[0].get("account"))["registered"]

    # global numbers
    # unreg_numbers = list(filter(is_unregistered, numbers))
    # if not unreg_numbers:
    #     numbers = [buy_number()]
    # else:
    #     numbrers = unreg_numbers

    try:
        numbers = open("numbers").read().strip().split(", ")
    except FileNotFoundError:
        numbers = [buy_number()]

    while numbers:
        target = numbers.pop(0)
        print(f"registering {target}...")
        datastore = SignalDatastore("1" + target)
        await datastore.account_interface.create_table()
        try:
            await datastore.download()
        except (Exception, AssertionError):
            pass
        # should check if it's already registered before buying a captcha...
        # await asyncio.sleep(1000)
        print("getting a captcha...")
        try:
            resp = await client_session.post(
                "https://human-after-all-21.fly.dev/6LedYI0UAAAAAMt8HLj4s-_2M_nYOhWMMFRGYHgY",
                data="https://signalcaptchas.org/registration/generate.html",
            )
        except aiohttp.client_exceptions.ServerDisconnectedError:
            print("server disconnected :/")
            sys.exit(1)
        rc_resp = (
            (await resp.json()).get("solution", {}).get("gRecaptchaResponse")
        )
        if not rc_resp:
            return
        print("captcha solution: " + rc_resp)
        shutils.rmtree("/tmp/signal-register")
        os.mkdir("/tmp/signal-register")
        os.chdir("/tmp/signal-register")
        tunnel = await set_pingback(target)
        try:
            cmd = f"./signal-cli --verbose --config . -u +1{target} register --captcha {rc_resp}".split()
            register = await asyncio.subprocess.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # await register.wait()
            (so, se) = await register.communicate()
            print("signal-cli register:", so.decode(), "\n", se.decode())
            if "Invalid captcha given" in so.decode():
                continue
            await datastore.upload()
            while True:
                verif = await app["sms_queue"].get()
                verif_msg = verif.get("message")
                print(verif_msg)
                code = (
                    re.search("\d\d\d-?\d\d\d", verif_msg)
                    .group()
                    .replace("-", "")
                )
                print(f"got code {code}", verif)
                if code:
                    break
            cmd = f"./signal-cli --verbose --config . -u +1{target} verify {code}".split()
            verify = await asyncio.subprocess.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await verify.wait()
            (so, se) = await verify.communicate()
            print(so, "\n", se)
            await datastore.upload()
        finally:
            print("terminating tunnel")
            tunnel.terminate()
    # await asyncio.sleep(1000)


if __name__ == "__main__":
    asyncio.run(local_main())
