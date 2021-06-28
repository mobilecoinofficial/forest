#!/usr/bin/python3.9
from typing import Optional
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import asyncio
import os
import re
import shutil
import json
import requests
from datastore import SignalDatastore, get_account_interface
from forest_tables import RoutingManager
import utils


def mod_pending_numbers(
    add: Optional[str] = None, rm: Optional[str] = None
) -> list[str]:
    try:
        existing_numbers = open("numbers").read().split(", ")
    except FileNotFoundError:
        existing_numbers = []
    added = [add] if add else []
    new_numbers = [number for number in existing_numbers if number != rm] + added
    open("numbers", "w").write(", ".join(new_numbers))
    return new_numbers


async def verify(sms_response: dict) -> None:
    if "message" not in sms_response:
        return
    verif_msg = sms_response["message"]
    print(verif_msg)
    match = re.search(r"\d\d\d-?\d\d\d", verif_msg)
    if not match:
        return
    code = match.group().replace("-", "")
    print(f"got code {code}", code)
    if not code:
        return
    if not verify:
        print(code)
        return
    number = utils.signal_format(sms_response["destination"])
    cmd = f"./signal-cli --verbose --config /tmp/signal-register -u {number} verify {code}".split()
    proc = await subprocess.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    (so, se) = await proc.communicate()
    print(so, "\n", se)
    cwd = os.getcwd()
    os.chdir("/tmp/signal-register")
    print(json.load(open(number))["registered"])
    datastore = SignalDatastore(number)
    await RoutingManager().delete(utils.teli_format(number))
    await datastore.upload()
    print("uploaded")
    os.chdir(cwd)
    mod_pending_numbers(rm=number)
    return


async def register_number(
    raw_number: str, timeout: int = 300, force: bool = False
) -> bool:
    number = utils.signal_format(raw_number)
    print(f"registering {number}")
    datastore = SignalDatastore(number)
    await datastore.account_interface.create_table()
    print("made db..")
    if not force and await datastore.is_registered():
        print("alranddy registered")
        mod_pending_numbers(rm=number)
        return False
    receiver = utils.ReceiveSMS()
    async with utils.ReceiveSMS().receive() as _, utils.get_url() as url:
        print(utils.set_sms_url(number, url))
        captcha = utils.get_signal_captcha()
        if not captcha:
            return False
        try:
            shutil.rmtree("/tmp/signal-register")
        except FileNotFoundError:
            pass
        os.mkdir("/tmp/signal-register")
        cmd = (
            "./signal-cli --verbose --config /tmp/signal-register "
            f"-u {number} register --captcha {captcha}"
        ).split()
        register = await subprocess.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        (so, se) = await register.communicate()
        if so or se:
            print("signal-cli register:", so.decode(), "\n", se.decode())
        else:
            print("no output from signal-cli register")
        if "Invalid captcha given" in so.decode():
            input("press enter when you've written a new captcha to /tmp/captcha")
            return await register_number(raw_number, timeout, force)
        for i in range(timeout):
            msg = await receiver.msgs.get()
            print(msg)
            verify(msg)
            if await datastore.is_registered():
                return True
            await asyncio.sleep(1)
        print("timed out waiting for verification sms")
        return False


# async def add_device(uri: str):
#     cmd = f"./signal-cli --config . addDevice --uri {uri}"

def get_unregistered_numbers() -> list[str]:
    blob = requests.get(
        "https://apiv1.teleapi.net/user/dids/list",
        params={"token": utils.get_secret("TELI_KEY")},
    ).json()
    accounts = get_account_interface()
    return [
        did["number"]
        for did in blob["data"]
        if "through-the-trees" not in did["sms_post_url"]
        and not accounts.is_registered("1" + did["number"])
    ]


async def main() -> None:
    pending_numbers = mod_pending_numbers()
    for number in pending_numbers:
        if await register_number(number, force=True):
            return
    # if any(map(register_number, utils.list_our_numbers())):
    #    return
    available_numbers = utils.search_numbers(nxx="617", limit=1)
    new_number = available_numbers[0]
    if input(f"buy {new_number}? ") != "yes":
        print("not buying number")
        return
    routing_manager = RoutingManager()
    await routing_manager.intend_to_buy(new_number)
    resp = utils.buy_number(new_number)
    if "error" in resp:
        print(resp)
        routing_manager.delete(new_number)
    await routing_manager.mark_bought(new_number)
    mod_pending_numbers(add=new_number)
    register_number(new_number)
    return


if __name__ == "__main__":
    asyncio.run(main())
