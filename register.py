#!/usr/bin/python3.9
from typing import Optional
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import asyncio
import os
import re
import shutil
import phonenumbers as pn
from datastore import SignalDatastore
import utils


def mod_pending_numbers(
    add: Optional[str] = None, rm: Optional[str] = None
) -> list[str]:
    try:
        existing_numbers = open("numbers").read().split(", ")
    except FileNotFoundError:
        existing_numbers = []
    new_numbers = (
        [number for number in existing_numbers if number != rm] + [add]
        if add
        else []
    )
    open("numbers", "w").write(", ".join(new_numbers))
    return new_numbers


async def register_number(
    raw_number: str, verify: bool = True, timeout: int = 60
) -> bool:
    number = utils.signal_format(raw_number)
    datastore = SignalDatastore(number.lstrip("+"))
    await datastore.account_interface.create_table()
    if await datastore.is_registered():
        print("already registered")
        mod_pending_numbers(rm=number)
        return False

    async def do_verify(sms_response: dict) -> None:
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
        cmd = (
            f"./signal-cli --verbose --config /tmp/signal-register -u {number} verify {code}".split()
        )
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
        await datastore.upload()
        os.chdir(cwd)
        mod_pending_numbers(rm=number)
        return

    receiver = utils.ReceiveSMS(callback=do_verify)
    async with receiver.receive() as server, utils.get_url() as url:
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
        print("signal-cli register:", so.decode(), "\n", se.decode())
        if "Invalid captcha given" in so.decode():
            return False
        for _ in range(timeout):
            if await datastore.is_registered():
                return True
            await asyncio.sleep(1)
        return False


async def main() -> None:
    pending_numbers = mod_pending_numbers()
    # as soon as a registration is successful, the iterator will end
    if any(map(register_number, pending_numbers)):
        return
    # if any(map(register_number, utils.list_our_numbers())):
    #    return
    available_numbers = utils.search_numbers(nxx="617", limit=1)
    new_number = available_numbers[0]
    if input(f"buy {new_number}? ") != "yes":
        print("not buying number")
        return
    utils.buy_number(new_number)
    mod_pending_numbers(add=new_number)
    register_number(new_number)
    return


if __name__ == "__main__":
    asyncio.run(main())
