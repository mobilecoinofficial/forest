#!/usr/bin/python3.9
import asyncio
import os
import aioprocessing
import aiohttp
from aiohttp import web
import re
import json
import urllib
import urllib.parse
from requests import get
import main
import forest_tables

# avaiable options: state, npa (area code), nxx(midlde three digits), search (720***test will search for 720***8378)
# number (string, required)
# sms_post_url (string, optional)

# load secrets file into environment before launching main
os.environ.update(
    {
        x[0]: x[1]
        for x in (line.split("=", 1) for line in open("secrets").read().split())
    }
)
TELI_KEY = os.environ.get("TELI_KEY")
if not TELI_KEY:
    raise ValueError("Missing Teli Key")

# numbers = [
#     "7252203411",
#     "7252203412",
# ]

try:
    numbers = open("numbers").read().strip().split(", ")
except FileNotFoundError:
    numbers = []


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
        return 
    # buy {new_number}? y/n (response/react)
    url = f"https://apiv1.teleapi.net/dids/order?token={TELI_KEY}&number={available_numbers[0]}"
    print(url)
    resp = get(url)
    print(resp.text)
    print(resp)
    open("numbers", "w").write(", ".join(numbers + [new_number]))


# https://apidocs.teleapi.net/api/my-phone-numbers/set-call-forwarding
#
# https://apiv1.teleapi.net/user/dids/smsurl/set

# token (string, required)
# did_id (int, required)
# url (string, required)


async def inbound_handler(request):
    msg_data = await request.text()
    # parse query-string encoded sms/mms into object
    msg_obj = {x: y[0] for x, y in urllib.parse.parse_qs(msg_data).items()}
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


tunnel = None
async def local_main():
    #    await main.start_sessions(globals())
    client_session = aiohttp.ClientSession()
    user_manager_connection = forest_tables.UserManager()
    await user_manager_connection.create_table()
    await start_app()
    async def set_pingback(target):
        did_lookup = await (
            await client_session.get(
                f"https://apiv1.teleapi.net/user/dids/get?token={TELI_KEY}&number={target}"
            )
        ).json()
        print(did_lookup)
        did_id = did_lookup.get("data").get("id")
        # if retcode==127: exec("sudo npm install -g localtunnel")
        global tunnel
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

    global numbers
    unreg_numbers = [
        number
        for number in numbers
        if not (user := (await user_manager_connection.get_user(f"1{number}")))
        or not json.loads(user[0].get("account"))["registered"]
    ]
    if not unreg_numbers:
        numbers = [buy_number()]
    else:
        numbrers = unreg_numbers

    while numbers:
        target = numbers.pop(0)
        print(f"registering {target}...")
        await set_pingback(target)
        # await asyncio.sleep(1000)
        print("getting a captcha...")
        resp = await client_session.post(
            "https://human-after-all-21.fly.dev/6LedYI0UAAAAAMt8HLj4s-_2M_nYOhWMMFRGYHgY",
            data="https://signalcaptchas.org/registration/generate.html",
        )
        rc_resp = (
            (await resp.json()).get("solution", {}).get("gRecaptchaResponse")
        )
        print("captcha solution: " + rc_resp)
        if rc_resp:
            cmd = f"./signal-cli --verbose --config . -u +1{target} register --captcha {rc_resp}".split()
            register = await asyncio.subprocess.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # await register.wait()
            (so, se) = await register.communicate()
            print("signal-cli register:", so.decode(), "\n", se.decode())
            await user_manager_connection.put_user(
                f"1{target}", open(f"data/+1{target}").read()
            )
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
            await user_manager_connection.set_user(
                f"1{target}", open(f"data/+1{target}").read()
            )
    # await asyncio.sleep(1000)


if __name__ == "__main__":
    try:
        asyncio.run(local_main())
    finally:
        if tunnel:
            tunnel.terminate()
            print("killed tunnel")
