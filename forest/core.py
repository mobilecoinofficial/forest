#!/usr/bin/python3.9
"""
The core chatbot framework: Message, Signal, Bot, and app
"""
import asyncio
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import json
import logging
import os
import signal
import sys
from asyncio import Queue, StreamReader, StreamWriter
from asyncio.subprocess import PIPE
from typing import Any, AsyncIterator, Optional, Union

import aiohttp
import phonenumbers as pn
import termcolor
from aiohttp import web
from phonenumbers import NumberParseException

# framework
from forest import datastore
from forest import autosave
from forest import pghelp
from forest import utils
from forest import mc_util
from forest import payments_monitor

JSON = dict[str, Any]
Response = Union[str, list, dict[str, str], None]


class Message:
    """Represents a Message received from signal-cli, optionally
    containing a command with arguments, group, quote, or payment"""

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        self.envelope = envelope = blob.get("envelope", {})
        # {'envelope': {'source': '+15133278483', 'sourceDevice': 2, 'timestamp': 1621402445257, 'receiptMessage': {'when': 1621402445257, 'isDelivery': True, 'isRead': False, 'timestamps': [1621402444517]}}}

        # envelope data
        self.source: str = envelope.get("source")
        self.name: str = envelope.get("sourceName") or self.source
        self.timestamp = envelope.get("timestamp")
        self.typing = envelope.get("typingMessage", {}).get("action")

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
            self.command = command[1:].lower()  # remove /
            self.arg1 = self.tokens[0] if self.tokens else None
            self.text = " ".join(self.tokens)
        # self.reactions: dict[str, str] = {}

    def __repr__(self) -> str:
        # it might be nice to prune this so the logs are easier to read
        return f"<{self.envelope}>"


class Signal:
    """
    Represents a signal-cli session.
    Lifecycle: Downloads the datastore, runs and restarts signal-cli,
    tries to gracefully kill signal-cli and upload before exiting.
    I/O: reads signal-cli's output into signalcli_output_queue,
    has methods for sending commands to signal-cli, and
    actually writes those json blobs to signal-cli's stdin.
    """

    def __init__(self, bot_number: Optional[str] = None) -> None:
        if not bot_number:
            try:
                bot_number = utils.signal_format(sys.argv[1])
                assert bot_number is not None
            except IndexError:
                bot_number = utils.get_secret("BOT_NUMBER")
        logging.debug("bot number: %s", bot_number)
        self.bot_number = bot_number
        self.datastore = datastore.SignalDatastore(bot_number)
        self.proc: Optional[subprocess.Process] = None
        self.signalcli_output_queue: Queue[Message] = Queue()
        self.signalcli_input_queue: Queue[dict] = Queue()
        self.exiting = False

    async def start_process(self) -> None:
        """
        Add SIGINT handlers. Download datastore. Maybe set profile.
        (Re)start signal-cli and launch reading and writing with it.
        """
        # things that don't work: loop.add_signal_handler(async_shutdown) - TypeError
        # signal.signal(sync_signal_handler) - can't interact with loop
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self.sync_signal_handler)
        logging.debug("added signal handler, downloading...")
        if utils.DOWNLOAD:
            await self.datastore.download()
        if utils.get_secret("PROFILE"):
            await self.set_profile()
        write_task: Optional[asyncio.Task] = None
        while self.sigints == 0 and not self.exiting:
            command = f"{utils.ROOT_DIR}/signal-cli --config {utils.ROOT_DIR} --output=json --user {self.bot_number} stdio".split()
            logging.info(command)
            self.proc = await asyncio.create_subprocess_exec(
                *command, stdin=PIPE, stdout=PIPE
            )
            logging.info(
                "started signal-cli @ %s with PID %s",
                self.bot_number,
                self.proc.pid,
            )
            assert self.proc.stdout and self.proc.stdin
            asyncio.create_task(self.handle_signalcli_raw_output(self.proc.stdout))
            # prevent the previous signal-cli's write task from stealing commands from the input queue
            if write_task:
                write_task.cancel()
            write_task = asyncio.create_task(self.write_commands(self.proc.stdin))
            returncode = await self.proc.wait()
            logging.warning("signal-cli exited: %s", returncode)
            if returncode == 0:
                logging.info("signal-cli apparently exited cleanly, not restarting")
                break

    sigints = 0

    def sync_signal_handler(self, *_: Any) -> None:
        """Try to start async_shutdown and/or just sys.exit"""
        logging.info("handling sigint. sigints: %s", self.sigints)
        self.sigints += 1
        self.exiting = True
        try:
            loop = asyncio.get_running_loop()
            logging.info("got running loop, scheduling async_shutdown")
            asyncio.run_coroutine_threadsafe(self.async_shutdown(), loop)
        except RuntimeError:
            asyncio.run(self.async_shutdown())
        if self.sigints >= 3:
            sys.exit(1)

    async def async_shutdown(self, *_: Any, wait: bool = False) -> None:
        """Upload our datastore, close postgres connections pools, kill signal-cli, exit"""
        logging.info("starting async_shutdown")
        # if we're downloading, then we upload too
        if utils.UPLOAD:
            await self.datastore.upload()
        if self.proc:
            try:
                self.proc.kill()
                if wait and utils.UPLOAD:
                    await self.proc.wait()
                    await self.datastore.upload()
            except ProcessLookupError:
                logging.info("no signal-cli process")
        if utils.UPLOAD:
            await self.datastore.mark_freed()
        await pghelp.close_pools()
        # this still deadlocks. see https://github.com/forestcontact/forest-draft/issues/10
        if autosave._memfs_process:
            executor = autosave._memfs_process._get_executor()
            logging.info(executor)
            executor.shutdown(wait=False, cancel_futures=True)
        logging.info("exited".center(60, "="))
        sys.exit(0)
        logging.info("called sys.exit but still running, trying os._exit")
        os._exit(1)

    async def handle_signalcli_raw_output(self, stream: StreamReader) -> None:
        """Read signal-cli output but delegate handling it"""
        while True:
            line = (await stream.readline()).decode().strip()
            if not line:
                break
            await self.handle_signalcli_raw_line(line)
        logging.info("stopped reading signal-cli stdout")

    async def handle_signalcli_raw_line(self, line: str) -> None:
        """Try to parse a single line of signal-cli output. If it's a message, enqueue it"""
        # TODO: maybe output and color non-json. pretty-print errors
        # unrelatedly, try to sensibly color the other logging stuff like http logs
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
                # maybe also send this to admin as a signal message
                for _line in tb:
                    logging.error(_line)
            else:
                logging.error(
                    "signal-cli error blob: %s", termcolor.colored(blob["error"], "red")
                )
            return
        msg = Message(blob)
        if msg.full_text:
            logging.info("signal: %s", line)
        await self.signalcli_output_queue.put(msg)
        return

    # i'm tempted to refactor these into handle_messages
    async def signalcli_output_iter(self) -> AsyncIterator[Message]:
        """Provides an asynchronous iterator over messages on the queue.
        See Bot for how messages and consumed and dispatched"""
        while True:
            message = await self.signalcli_output_queue.get()
            yield message

    # Next, we see how the input queue is populated and consumed.

    async def set_profile(self) -> None:
        """Set signal profile. Note that this will overwrite any mobilecoin address"""
        env = utils.get_secret("ENV")
        profile = {
            "command": "updateProfile",
            "given-name": "localbot" if utils.LOCAL else "forestbot",
            "family-name": "" if env == "prod" else env,  # maybe not?
            "avatar": "avatar.png",
        }
        await self.signalcli_input_queue.put(profile)
        logging.info(profile)

    async def send_message(  # pylint: disable=too-many-arguments
        self,
        recipient: Optional[str],
        msg: Response,
        group: Optional[str] = None,  # maybe combine this with recipient?
        endsession: bool = False,
        attachments: Optional[list[str]] = None,
    ) -> None:
        """Builds send command with specified recipient and msg, writes to signal-cli.
        Prettyprints dict msgs, sends multiple messages for lists. Also does groups,
        attachments, and endsession"""
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
        if attachments:
            json_command["attachment"] = attachments
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

    async def respond(self, target_msg: Message, msg: Response) -> None:
        """Respond to a message depending on whether it's a DM or group"""
        if target_msg.group:
            await self.send_message(None, msg, group=target_msg.group)
        else:
            await self.send_message(target_msg.source, msg)

    async def send_reaction(self, target_msg: Message, emoji: str) -> None:
        """Send a reaction. Protip: you can use e.g. \N{GRINNING FACE} in python"""
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

    async def signalcli_input_iter(self) -> AsyncIterator[dict]:
        """Provides an asynchronous iterator over pending signal-cli commands"""
        while True:
            command = await self.signalcli_input_queue.get()
            yield command

    # maybe merge with the above?
    async def write_commands(self, pipe: StreamWriter) -> None:
        """Encode and write pending signal-cli commands"""
        async for msg in self.signalcli_input_iter():
            logging.info("input to signal: %s", msg)
            if pipe.is_closing():
                logging.error("signal-cli stdin pipe is closed")
            pipe.write(json.dumps(msg).encode() + b"\n")


class Bot(Signal):
    """Handles messages and command dispatch, as well as basic commands.
    Must be instantiated within a running async loop.
    Subclass this with your own commands.
    """

    def __init__(self, *args: str) -> None:
        """Creates AND STARTS a bot that routes commands to do_x handlers"""
        self.client_session = aiohttp.ClientSession()
        self.mobster = payments_monitor.Mobster()
        self.pongs: dict[str, str] = {}
        super().__init__(*args)
        asyncio.create_task(self.start_process())
        asyncio.create_task(self.handle_messages())
        if utils.get_secret("MONITOR_WALLET"):
            # currently spams and re-credits the same invoice each reboot
            asyncio.create_task(self.mobster.monitor_wallet())

    async def handle_messages(self) -> None:
        """Read messages from the queue and pass each message to handle_message
        If that returns a non-empty string, send it as a response"""
        async for message in self.signalcli_output_iter():
            # potentially stick a try-catch block here and send errors to admin
            response = await self.handle_message(message)
            if response:
                await self.respond(message, response)

    async def handle_message(self, message: Message) -> Response:
        """Method dispatch to do_x commands and goodies.
        Overwrite this to add your own non-command logic,
        but call super().handle_message(message) at the end"""
        if message.command:
            if hasattr(self, "do_" + message.command):
                return await getattr(self, "do_" + message.command)(message)
            suggest_help = " Try /help." if hasattr(self, "do_help") else ""
            return f"Sorry! Command {message.command} not recognized!" + suggest_help
        if message.text == "TERMINATE":
            return "signal session reset"
        if message.payment:
            asyncio.create_task(self.handle_payment(message))
            return None
        return await self.default(message)

    async def default(self, message: Message) -> Response:
        resp = "That didn't look like a valid command"
        # if it messages an echoserver, don't get in a loop
        if message.text and not (message.group or message.text == resp):
            return resp
        return None

    async def do_help(self, message: Message) -> str:
        """List available commands. /help <command> gives you that command's documentation, if available"""
        if message.arg1:
            if hasattr(self, "do_" + message.arg1):
                cmd = getattr(self, "do_" + message.arg1)
                if cmd.__doc__:
                    return cmd.__doc__
                return f"Sorry, {message.arg1} isn't documented"
        # TODO: filter aliases and indicate which commands are undocumented
        return "commands: " + ", ".join(
            k.removeprefix("do_") for k in dir(self) if k.startswith("do_")
        )

    async def do_printerfact(self, _: Message) -> str:
        "Learn a fact about printers"
        async with self.client_session.get("https://colbyolson.com/printers") as resp:
            fact = await resp.text()
        return fact.strip()

    async def do_ping(self, message: Message) -> str:
        """returns to /ping with /pong"""
        if message.text:
            return f"/pong {message.text}"
        return "/pong"

    async def do_pong(self, message: Message) -> str:
        if message.text:
            self.pongs[message.text] = message.text
            return f"OK, stashing {message.text}"
        return "OK"

    async def check_target_number(self, msg: Message) -> Optional[str]:
        """Check if arg1 is a valid number. If it isn't, let the user know and return None"""
        try:
            logging.debug("checking %s", msg.arg1)
            assert msg.arg1
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

    async def handle_payment(self, message: Message) -> None:
        """Decode the receipt, then update balances.
        Blocks on transaction completion, run concurrently"""
        logging.info(message.payment)
        amount_pmob = await self.mobster.get_receipt_amount_pmob(
            message.payment["receipt"]
        )
        if amount_pmob is None:
            await self.respond(
                message, "That looked like a payment, but we couldn't parse it"
            )
            return
        amount_mob = mc_util.pmob2mob(amount_pmob)
        amount_usd_cents = round(amount_mob * await self.mobster.get_rate() * 100)
        await self.mobster.ledger_manager.put_pmob_tx(
            message.source,
            amount_usd_cents,
            amount_pmob,
            message.payment.get("note"),
        )
        await self.respond(
            message,
            f"Thank you for sending {float(amount_mob)} MOB ({amount_usd_cents/100} USD)",
        )
        await self.respond(message, await self.payment_response(message))

    async def payment_response(self, _: Message) -> Response:
        return "This bot doesn't have a response for payments."


async def no_get(request: web.Request) -> web.Response:
    raise web.HTTPFound(location="https://signal.org/")


async def pong_handler(request: web.Request) -> web.Response:
    pong = request.match_info.get("pong")
    session = request.app.get("bot")
    if not session:
        return web.Response(status=504, text="Sorry, no live workers.")
    pong = session.pongs.pop(pong, "")
    if pong == "":
        return web.Response(status=404, text="Sorry, can't find that key.")
    return web.Response(status=200, text=pong)


async def send_message_handler(request: web.Request) -> web.Response:
    """Allow webhooks to send messages to users.
    Turn this off, authenticate, or obfuscate in prod to someone from using your bot to spam people
    """
    account = request.match_info.get("phonenumber")
    session = request.app.get("bot")
    if not session:
        return web.Response(status=504, text="Sorry, no live workers.")
    msg_data = await request.text()
    await session.send_message(
        account, msg_data, endsession=request.query.get("endsession")
    )
    return web.json_response({"status": "sent"})


app = web.Application()

app.add_routes(
    [
        web.get("/", no_get),
        web.get("/pongs/{pong}", pong_handler),
        web.post("/user/{phonenumber}", send_message_handler),
    ]
)


# order of operations:
# 1. start memfs
# 2. instanciate Bot, which calls setup_tmpdir
# 3. download
# 4. start process 

if utils.MEMFS:
    app.on_startup.append(autosave.start_memfs)
    app.on_startup.append(autosave.start_memfs_monitor)


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = Bot()

    web.run_app(app, port=8080, host="0.0.0.0")
