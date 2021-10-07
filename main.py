#!/usr/bin/python3.9
import asyncio
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import json
import logging
import os
import random
import signal
import sys
import time
from asyncio import Queue
from asyncio.subprocess import PIPE
from functools import lru_cache
from typing import Any, AsyncIterator, Optional, Union

import aiohttp
import phonenumbers as pn
import termcolor
from aiohttp import web
from phonenumbers import NumberParseException

# framework
import datastore
import pghelp

# biz logic
import teli
import payments_monitor
import utils
from forest_tables import GroupRoutingManager, PaymentsManager, RoutingManager

JSON = dict[str, Any]
Response = Union[str, list, dict[str, str], None]


# h/t https://stackoverflow.com/questions/31771286/python-in-memory-cache-with-time-to-live
def get_ttl_hash(seconds: int = 3600) -> int:
    """Return the same value withing `seconds` time period"""
    return round(time.time() / seconds)


class Message:
    """Represents a Message received from signal-cli, optionally containing a command with arguments."""

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        self.envelope = envelope = blob.get("envelope", {})
        # {'envelope': {'source': '+15133278483', 'sourceDevice': 2, 'timestamp': 1621402445257, 'receiptMessage': {'when': 1621402445257, 'isDelivery': True, 'isRead': False, 'timestamps': [1621402444517]}}}

        # envelope data
        self.source: str = envelope.get("source")
        self.name: str = envelope.get("sourceName") or self.source
        self.timestamp = envelope.get("timestamp")

        # msg data
        msg = envelope.get("dataMessage", {})
        self.full_text = self.text = msg.get("message", "")
        self.group: Optional[str] = msg.get("groupInfo", {}).get("groupId")
        self.quoted_text = msg.get("quote", {}).get("text")
        self.payment = msg.get("payment")

        # parsing
        self.command: Optional[str] = None
        self.tokens: Optional[list[str]] = None
        if self.text and self.text.startswith("/"):
            command, *self.tokens = self.text.split(" ")
            self.command = command[1:]  # remove /
            self.arg1 = self.tokens[0] if self.tokens else None
            self.text = " ".join(self.tokens[1:]) if len(self.tokens) > 1 else None
        # self.reactions: dict[str, str] = {}

    def __repr__(self) -> str:
        # it might be nice to prune this so the logs are easier to read
        return f"<{self.envelope}>"


class Signal:
    """
    Represents a signal-cli session
    Creates database connections for managing signal keys and payments.
    """

    def __init__(self, bot_number: str) -> None:
        logging.debug("bot number: %s", bot_number)
        self.bot_number = bot_number
        self.datastore = datastore.SignalDatastore(bot_number)
        self.proc: Optional[subprocess.Process] = None
        self.signalcli_output_queue: Queue[Message] = Queue()
        self.signalcli_input_queue: Queue[dict] = Queue()

    async def send_message(
        self,
        recipient: Optional[str],
        msg: Response,
        group: Optional[str] = None,
        endsession: bool = False,
    ) -> None:
        """Builds send command with specified recipient and msg, writes to signal-cli."""
        if isinstance(msg, list):
            for m in msg:
                await self.send_message(recipient, m)
            return
        if isinstance(msg, dict):
            msg = "\n".join((f"{key}:\t{value}" for key, value in msg.items()))
        json_command: JSON = {
            "command": "send",
            "message": msg,
        }
        if endsession:
            json_command["endsession"] = True
        if group:
            json_command["group"] = group
        elif recipient:
            try:
                assert recipient == utils.signal_format(recipient)
            except (AssertionError, NumberParseException) as e:
                logging.error(e)
                return
            json_command["recipient"] = [str(recipient)]
        await self.signalcli_input_queue.put(json_command)
        return

    async def respond(self, target_msg: Message, msg: Union[str, list, dict]) -> None:
        if target_msg.group:
            await self.send_message(None, msg, group=target_msg.group)
        else:
            await self.send_message(target_msg.source, msg)

    async def send_reaction(self, target_msg: Message, emoji: str) -> None:
        react = {
            "command": "sendReaction",
            "emoji": emoji,
            "target-author": target_msg.source,
            "target-timestamp": target_msg.timestamp,
        }
        if target_msg.group:
            react["group"] = target_msg.group
        else:
            react["recipient"] = [target_msg.source]
        await self.signalcli_input_queue.put(react)

    async def signalcli_output_iter(self) -> AsyncIterator[Message]:
        """Provides an asynchronous iterator over messages on the queue."""
        while True:
            message = await self.signalcli_output_queue.get()
            yield message

    async def signalcli_input_iter(self) -> AsyncIterator[dict]:
        """Provides an asynchronous iterator over pending signal-cli commands"""
        while True:
            command = await self.signalcli_input_queue.get()
            yield command

    async def on_startup(self) -> None:
        profile = {
            "command": "updateProfile",
            "given-name": "localbot" if utils.LOCAL else "forestbot",
            "family-name": utils.get_secret("ENV"),  # maybe not
            "avatar": "avatar.png",
        }
        await self.signalcli_input_queue.put(profile)
        logging.info(profile)

    async def launch_and_connect(self) -> None:
        logging.debug("in launch_and_connect")
        loop = asyncio.get_running_loop()
        logging.debug("got running loop")
        # things that don't work: loop.add_signal_handler(async_shutdown) - TypeError
        # signal.signal(sync_signal_handler) - can't interact with loop
        loop.add_signal_handler(signal.SIGINT, self.sync_signal_handler)

        # the thing we want here is to check for a data/ dir in the current directory
        # if so, also upload it but don't download it now (unless..?)
        # if not, download it and stay synced

        logging.debug("added signal handler, downloading...")
        if not utils.get_secret("NO_DOWNLOAD"):
            await self.datastore.download()
        command = f"{utils.ROOT_DIR}/signal-cli --config {utils.ROOT_DIR} --output=json stdio".split()
        logging.info(command)
        self.proc = await asyncio.create_subprocess_exec(
            *command, stdin=PIPE, stdout=PIPE
        )
        # while 1: await self.proc.wait(); self.proc = await asyncio.create_subprocess_exec ...
        logging.info(
            "started signal-cli @ %s with PID %s",
            self.bot_number,
            self.proc.pid,
        )
        await self.on_startup()
        assert self.proc.stdout and self.proc.stdin
        asyncio.create_task(
            self.listen_to_signalcli(
                self.proc.stdout,
                self.signalcli_output_queue,
            )
        )
        async for msg in self.signalcli_input_iter():
            logging.info("input to signal: %s", msg)
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
        await self.proc.wait()

    async def async_shutdown(self, *_: Any, wait: bool = False) -> None:
        logging.info("starting async_shutdown")
        await self.datastore.upload()
        if self.proc:
            try:
                self.proc.kill()
                if wait:
                    await self.proc.wait()
                    await self.datastore.upload()
            except ProcessLookupError:
                logging.info("no process")
        await self.datastore.mark_freed()
        await pghelp.close_pools()
        # this doesn't work. see https://github.com/forestcontact/forest-draft/issues/10
        if datastore._memfs_process:
            executor = datastore._memfs_process._get_executor()
            logging.info(executor)
            executor.shutdown(wait=False, cancel_futures=True)
        logging.info("=============exited===================")
        sys.exit(0)
        logging.info(
            "called sys.exit but still running, os.kill sigint to %s",
            os.getpid(),
        )
        os.kill(os.getpid(), signal.SIGINT)
        logging.info("still running after os.kill, trying os._exit")
        os._exit(1)

    sigints = 0

    def sync_signal_handler(self, *_: Any) -> None:
        logging.info("handling sigint. sigints: %s", self.sigints)
        self.sigints += 1
        try:
            loop = asyncio.get_running_loop()
            logging.info("got running loop, scheduling async_shutdown")
            asyncio.run_coroutine_threadsafe(self.async_shutdown(), loop)
        except RuntimeError:
            asyncio.run(self.async_shutdown())
        if self.sigints >= 3:
            sys.exit(1)
            raise KeyboardInterrupt
            logging.info("this should never get called")  # pylint: disable=unreachable

    async def listen_to_signalcli(
        self,
        stream: asyncio.StreamReader,
        queue: Queue[Message],
    ) -> None:
        while True:
            line = (await stream.readline()).decode().strip()
            if not line:
                break
            await self.handle_raw_signalcli_output(line, queue)

    async def handle_raw_signalcli_output(
        self, line: str, queue: Queue[Message]
    ) -> None:
        # if utils.get_secret("I_AM_NOT_A_FEDERAL_AGENT"):
        # logging.info("signal: %s", line)
        # TODO: don't print receiptMessage
        # color non-json. pretty-print errors
        # sensibly color web traffic, too?
        # fly / db / asyncio and other lib warnings / java / signal logic and networking
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            logging.info("signal: %s", line)
            return
        if not isinstance(blob, dict):  # e.g. a timestamp
            return
        if "error" in blob:
            if "traceback" in blob:
                exception, *tb = blob["traceback"].split("\n")
                logging.error(termcolor.colored(exception, "red"))
                for _line in tb:
                    logging.error(_line)
            else:
                logging.error(termcolor.colored(blob["error"], "red"))
            return
        if "group" in blob:
            # maybe this info should just be in Message and handled in Session
            # SMS with {number} via {number}
            their, our = blob["name"].removeprefix("SMS with ").split(" via ")
            # TODO: this needs to use number[0]
            await GroupRoutingManager().set_sms_route_for_group(
                teli.teli_format(their),
                teli.teli_format(our),
                blob["group"],
            )
            # cmd = {
            #     "command": "updateGroup",
            #     "group": blob["group"],
            #     "admin": message.source,
            # }
            logging.info("made a new group route from %s", blob)
            return
        msg = Message(blob)
        if msg.text:
            logging.info("signal: %s", line)
        await queue.put(msg)
        return


class Bot(Signal):
    def __init__(self, *args: str) -> None:
        self.client_session = aiohttp.ClientSession()
        super().__init__(*args)

    async def handle_messages(self) -> None:
        async for message in self.signalcli_output_iter():
            response = await self.handle_message(message)
            if response:
                await self.respond(message, response)

    async def handle_message(self, message: Message) -> Response:
        if message.command:
            if hasattr(self, "do_" + message.command):
                return await getattr(self, "do_" + message.command)(message)
            return f"Sorry! Command {message.command} not recognized! Try /help."
        if message.text == "TERMINATE":
            return "signal session reset"
        if message.text:
            return "That didn't look like a valid command"
        return None

    async def do_printerfact(self, _: Message) -> str:
        "Learn a fact about printers"
        async with self.client_session.get("https://colbyolson.com/printers") as resp:
            fact = await resp.text()
        return fact.strip()

    async def do_ping(self, _: Message) -> str:
        return "pong"

    async def check_target_number(self, msg: Message) -> Optional[str]:
        logging.debug("checking %s", msg.arg1)
        try:
            parsed = pn.parse(msg.arg1, "US")  # fixme: use PhoneNumberMatcher
            assert pn.is_valid_number(parsed)
            number = pn.format_number(parsed, pn.PhoneNumberFormat.E164)
            return number
        except (pn.phonenumberutil.NumberParseException, AssertionError):
            await self.send_message(
                msg.source,
                f"{msg.arg1} doesn't look a valid number or user. "
                "did you include the country code?",
            )
            return None


class Forest(Bot):
    def __init__(self, *args: str) -> None:
        self.teli = teli.Teli()
        self.scratch: dict[str, dict[str, Any]] = {"payments": {}}
        self.payments_manager = PaymentsManager()
        self.routing_manager = RoutingManager()
        super().__init__(*args)

    async def send_sms(
        self, source: str, destination: str, message_text: str
    ) -> dict[str, str]:
        """Send SMS via teliapi.net call and returns the response"""
        payload = {
            "source": source,
            "destination": destination,
            "message": message_text,
        }
        response = await self.client_session.post(
            "https://api.teleapi.net/sms/send?token=" + utils.get_secret("TELI_KEY"),
            data=payload,
        )
        response_json_all = await response.json()
        response_json = {
            k: v
            for k, v in response_json_all.items()
            if k in ("status", "segment_count")
        }  # hide how the sausage is made
        return response_json

    async def get_user_numbers(self, message: Message) -> list[str]:
        if message.source:
            maybe_routable = await self.routing_manager.get_id(message.source)
            return [registered.get("id") for registered in maybe_routable]
        return []

    async def handle_message(self, message: Message) -> Response:
        numbers = await self.get_user_numbers(message)
        if numbers and message.group and message.text:
            group = await group_routing_manager.get_sms_route_for_group(message.group)
            if group:
                await self.send_sms(
                    source=group[0].get("our_sms"),
                    destination=group[0].get("their_sms"),
                    message_text=message.text,
                )
                await self.send_reaction(message, "\N{Outbox Tray}")
                return None
            logging.warning("couldn't find the route for this group...")
        elif numbers and message.quoted_text:
            try:
                quoted = dict(
                    line.split(":\t", 1) for line in message.quoted_text.split("\n")
                )
            except ValueError:
                quoted = {}
            if quoted.get("destination") in numbers and quoted.get("source"):
                logging.info("sms destination from quote: %s", quoted["destination"])
                response = await self.send_sms(
                    source=quoted["destination"],
                    destination=quoted["source"],
                    message_text=message.text,
                )
                await self.send_reaction(message, "\N{Outbox Tray}")
                return response
            await self.send_reaction(message, "\N{Cross Mark}")
            return "Couldn't send that reply"
        if message.command == "register":
            asyncio.create_task(self.do_register(message))
        elif message.payment:
            if message.source not in self.scratch["payments"]:
                self.scratch["payments"][message.source] = 0
            amount = payments_monitor.get_receipt_amount(message.payment["receipt"])
            self.scratch["payments"][message.source] += amount
            self.respond(message, f"Thank you for sending {amount} MOB")
            diff = self.scratch["payments"][message.source] - await self.get_price(
                ttl_hash=get_ttl_hash
            )
            if diff < 0:
                return "Please send another {abs(diff)} MOB to buy a phone number"
            if diff == 0:
                return "Thank you for paying! You can now buy a phone number with /order <area code>"
            return "Thank you for paying! You've overpayed by {diff}. Contact an administrator for a refund"
        return await Bot.handle_message(self, message)

    async def do_help(self, _: Message) -> str:
        return (
            "Welcome to the Forest.contact Pre-Release!\n"
            "To get started, try /register, or /status! "
            "If you've already registered, try to send a message via /send."
            ""
        )

    async def do_status(self, message: Message) -> Union[list[str], str]:
        numbers: list[str] = [
            registered.get("id")
            for registered in await self.routing_manager.get_id(message.source)
        ]
        # paid but not registered
        if self.scratch["payments"].get(message.source) and not numbers:
            return [
                "Welcome to the beta! Thank you for your payment. Please contact support to finish setting up your account by requesting to join this group. We will reach out within 12 hours.",
                "https://signal.group/#CjQKINbHvfKoeUx_pPjipkXVspTj5HiTiUjoNQeNgmGvCmDnEhCTYgZZ0puiT-hUG0hUUwlS",
                #    "Alternatively, try /order <area code>",
            ]
        if numbers and len(numbers) == 1:
            # registered, one number
            return f'Hi {message.name}! We found {numbers[0]} registered for your user. Try "/send {message.source} Hello from Forest Contact via {numbers[0]}!".'
        # registered, many numbers
        if numbers:
            return f"Hi {message.name}! We found several numbers {numbers} registered for your user. Try '/send {message.source} Hello from Forest Contact via {numbers[0]}!'."
        # not paid, not registered
        return (
            "We don't see any Forest Contact numbers for your account!"
            " If you would like to register a new number, "
            'try "/register" and following the instructions.'
        )

    @lru_cache(maxsize=2)
    async def get_price(
        self, ttl_hash: Optional[int] = None, perturb: bool = False
    ) -> float:
        del ttl_hash  # to emphasize we don't use it and to shut pylint up
        # this needs to cached per-hour or something
        usdt_price = 4.0  # 15.00
        try:
            url = "https://big.one/api/xn/v1/asset_pairs/8e900cb1-6331-4fe7-853c-d678ba136b2f"
            last_val = await self.client_session.get(url)
            resp_json = await last_val.json()
            mob_rate = float(resp_json.get("data").get("ticker").get("close"))
        except (
            aiohttp.ClientError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            logging.error(e)
            # big.one goes down sometimes, if it does... make up a price
            mob_rate = 14
        if perturb:
            # perturb each price slightly
            mob_rate -= random.random() / 1000
        # invpico = 100000000000 # doesn't work in mixin
        invnano = 100000000
        nmob_price = int(usdt_price / mob_rate * invnano)
        mob_price_exact = round(nmob_price / invnano, 3)
        # dunno if we want to generate new wallets? what happens if a user overpays?
        return mob_price_exact

    async def do_register(self, message: Message) -> bool:
        """register for a phone number"""
        mob_price_exact = await self.get_price(ttl_hash=get_ttl_hash())
        nmob_price = mob_price_exact * 100000000
        responses = [
            f"The current price for a SMS number is {mob_price_exact}MOB/month. If you would like to continue, please send exactly...",
            f"{mob_price_exact}",
            "on Signal Pay, or to",
            "nXz8gbcAfHQQUwTHuQnyKdALe5oXKppDn9oBRms93MCxXkiwMPnsVRp19Vrmb1GX6HdQv7ms83StXhwXDuJzN9N7h3mzFnKsL6w8nYJP4q",
            "Upon payment, you will be able to select the area code for your new phone number!",
        ]
        await self.send_message(message.source, responses)
        # check for payments every 10s for 1hr
        for _ in range(360):
            payment_done = await self.payments_manager.get_payment(nmob_price * 1000)
            if payment_done:
                payment_done = payment_done[0]
                await self.send_message(
                    message.source,
                    [
                        "Thank you for your payment! Please save this transaction ID for your records and include it with any customer service requests. Without this payment ID, it will be harder to verify your purchase.",
                        f"{payment_done.get('transaction_log_id')}",
                        'Please finish setting up your account at your convenience with the "/status" command.',
                    ],
                )
                self.scratch["payments"][message.source] = payment_done.get(
                    "transaction_log_id"
                )
                return True
            await asyncio.sleep(10)
        return False

    async def do_pay(self, message: Message) -> str:
        if message.arg1 == "shibboleth":
            self.scratch["payments"][message.source] = True
            return "...thank you for your payment"
        if message.arg1 == "sibboleth":
            return "sending attack drones to your location"
        return "no"

    async def do_order(self, msg: Message) -> str:
        """usage: /order <area code>"""
        if not (msg.arg1 and len(msg.arg1) == 3 and msg.arg1.isnumeric()):
            return """usage: /order <area code>"""
        diff = self.scratch["payments"].get(msg.source, 0) < await self.get_price(
            ttl_hash=get_ttl_hash()
        )
        if diff < 0:
            # this needs to check if there are *unfulfilled* payments
            return "make a payment with /register first"
        await self.routing_manager.sweep_expired_destinations()
        available_numbers = [
            num
            for record in await self.routing_manager.get_available()
            if (num := record.get("id")).startswith(msg.arg1)
        ]
        if available_numbers:
            number = available_numbers[0]
            await self.send_message(msg.source, f"found {number} for you...")
        else:
            numbers = await self.teli.search_numbers(area_code=msg.arg1, limit=1)
            if not numbers:
                return "sorry, no numbers for that area code"
            number = numbers[0]
            await self.send_message(msg.source, f"found {number}")
            await self.routing_manager.intend_to_buy(number)
            buy_info = await self.teli.buy_number(number)
            await self.send_message(msg.source, f"bought {number}")
            if "error" in buy_info:
                await self.routing_manager.delete(number)
                return f"something went wrong: {buy_info}"
            await self.routing_manager.mark_bought(number)
        await self.teli.set_sms_url(number, utils.URL + "/inbound")
        await self.routing_manager.set_destination(number, msg.source)
        if await self.routing_manager.get_destination(number):
            self.scratch["payments"][msg.source] -= await self.get_price(
                ttl_hash=get_ttl_hash()
            )
            return f"you are now the proud owner of {number}"
        return "db error?"

    if not utils.get_secret("ORDER"):
        del do_order, do_pay

    async def do_send(self, message: Message) -> Union[str, dict]:
        numbers = await self.get_user_numbers(message)
        if not numbers:
            return "You don't have any numbers. Register with /register"
        sms_dest = await self.check_target_number(message)
        if not sms_dest:
            return "Couldn't parse that number"
        response = await self.send_sms(
            source=numbers[0],
            destination=sms_dest,
            message_text=message.text,
        )
        await self.send_reaction(message, "\N{Outbox Tray}")
        # sms_uuid = response.get("data")
        # TODO: store message.source and sms_uuid in a queue, enable https://apidocs.teleapi.net/api/sms/delivery-notifications
        #    such that delivery notifs get redirected as responses to send command
        return response

    do_msg = do_send

    async def do_mkgroup(self, message: Message) -> str:
        numbers = await self.get_user_numbers(message)
        target_number = await self.check_target_number(message)
        if not numbers:
            return "no"
        if not target_number:
            return ""
        cmd = {
            "output": "json",
            "command": "updateGroup",
            "member": [message.source],
            "admin": [message.source],
            "name": f"SMS with {target_number} via {numbers[0]}",
        }
        await self.signalcli_input_queue.put(cmd)
        await self.send_reaction(message, "\N{Busts In Silhouette}")
        return "invited you to a group"

    do_query = do_mkgroup
    if not utils.get_secret("GROUPS"):
        del do_mkgroup, do_query


async def start_session(our_app: web.Application) -> None:
    try:
        number = utils.signal_format(sys.argv[1])
    except IndexError:
        number = utils.get_secret("BOT_NUMBER")
    our_app["session"] = new_session = Forest(number)
    try:
        payments_monitor.get_address()
    except IndexError:
        payments_monitor.import_account()
    if utils.get_secret("MIGRATE"):
        logging.info("migrating db...")
        await new_session.routing_manager.migrate()
        rows = await new_session.routing_manager.execute(
            "SELECT id, destination FROM routing"
        )
        for row in rows if rows else []:
            if not utils.LOCAL:
                await new_session.teli.set_sms_url(
                    row.get("id"), utils.URL + "/inbound"
                )
            if (dest := row.get("destination")) :
                new_dest = utils.signal_format(dest)
                await new_session.routing_manager.set_destination(
                    row.get("id"), new_dest
                )
        await new_session.datastore.account_interface.migrate()
        await group_routing_manager.execute("DROP TABLE IF EXISTS group_routing")
        await group_routing_manager.create_table()
    asyncio.create_task(new_session.launch_and_connect())
    asyncio.create_task(new_session.handle_messages())


# class Server: # or Inbound?


async def noGet(request: web.Request) -> web.Response:
    raise web.HTTPFound(location="https://signal.org/")


async def inbound_sms_handler(request: web.Request) -> web.Response:
    session = request.app.get("session")
    msg_data: dict[str, str] = dict(await request.post())  # type: ignore
    if not session:
        # no live worker sessions
        # if we can't get a signal delivery receipt/bad session, we could
        # return non-200 and let teli do our retry
        # however, this would require awaiting output from signal; tricky
        await request.app["client_session"].post(
            "https://counter.pythia.workers.dev/post", data=msg_data
        )
        return web.Response(status=504, text="Sorry, no live workers.")
    sms_destination = msg_data.get("destination")
    # lookup sms recipient to signal recipient
    maybe_signal_dest = await RoutingManager().get_destination(sms_destination)
    maybe_group = await group_routing_manager.get_group_id_for_sms_route(
        msg_data.get("source"), msg_data.get("destination")
    )
    if maybe_group:
        # if we can't notice group membership changes,
        # we could check if the person is still in the group
        logging.info("sending a group")
        group = maybe_group[0].get("group_id")
        # if it's a group, the to/from is already in the group name
        text = msg_data.get("message", "<empty message>")
        await session.send_message(None, text, group=group)
    elif maybe_signal_dest:
        recipient = maybe_signal_dest[0].get("destination")
        # send hashmap as signal message with newlines and tabs and stuff
        keep = ("source", "destination", "message")
        msg_clean = {k: v for k, v in msg_data.items() if k in keep}
        await session.send_message(recipient, msg_clean)
    else:
        logging.info("falling back to admin")
        if not msg_data:
            msg_data["text"] = await request.text()
        recipient = utils.get_secret("ADMIN")
        msg_data[
            "note"
        ] = "fallback, signal destination not found for this sms destination"
        if (agent := request.headers.get("User-Agent")) :
            msg_data["user-agent"] = agent
        # send the admin the full post body, not just the user-friendly part
        await session.send_message(recipient, msg_data)
    return web.Response(text="TY!")


# class Server: # or Inbound?


async def send_message_handler(request: web.Request) -> web.Response:
    account = request.match_info.get("phonenumber")
    session = request.app.get("session")
    if not session:
        return web.Response(status=504, text="Sorry, no live workers.")
    msg_data = await request.text()
    await session.send_message(
        account, msg_data, endsession=request.query.get("endsession")
    )
    return web.json_response({"status": "sent"})


app = web.Application()


app.on_startup.append(
    start_session
)  # cut this out. sessions can be attached to the app after the fact
if not utils.get_secret("NO_MEMFS"):
    app.on_startup.append(datastore.start_memfs)
    app.on_startup.append(datastore.start_memfs_monitor)
app.add_routes(
    [
        web.get("/", noGet),
        web.post("/inbound", inbound_sms_handler),
        web.post("/user/{phonenumber}", send_message_handler),
    ]
)

app["session"] = None


if __name__ == "__main__":
    logging.info("=========================new run=======================")
    group_routing_manager = GroupRoutingManager()
    web.run_app(app, port=8080, host="0.0.0.0")
