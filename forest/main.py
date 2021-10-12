#!/usr/bin/python3.9
import asyncio
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import json
import logging
import os
import signal
import sys
from asyncio import Queue
from asyncio.subprocess import PIPE
from typing import Any, AsyncIterator, Optional, Union

import aiohttp
import phonenumbers as pn
import termcolor
from aiohttp import web
from phonenumbers import NumberParseException

# framework
from forest import datastore
from forest import pghelp
from forest import utils

JSON = dict[str, Any]
Response = Union[str, list, dict[str, str], None]


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
            self.text = " ".join(self.tokens)
        # self.reactions: dict[str, str] = {}

    def __repr__(self) -> str:
        # it might be nice to prune this so the logs are easier to read
        return f"<{self.envelope}>"


class Signal:
    """
    Represents a signal-cli session
    Creates database connections for managing signal keys and payments.
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

    async def send_message(  # pylint: disable=too-many-arguments
        self,
        recipient: Optional[str],
        msg: Response,
        group: Optional[str] = None,
        endsession: bool = False,
        attachments: Optional[list[str]] = None,
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
        if attachments:
            json_command["attachments"] = attachments
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

    async def set_profile(self) -> None:
        profile = {
            "command": "updateProfile",
            "given-name": "localbot" if utils.LOCAL else "forestbot",
            "family-name": utils.get_secret("ENV"),  # maybe not
            "avatar": "avatar.png",
        }
        await self.signalcli_input_queue.put(profile)
        logging.info(profile)

    async def start_process(self) -> None:
        logging.debug("in start_process")
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
        if utils.get_secret("PROFILE"):
            await self.set_profile()
        assert self.proc.stdout and self.proc.stdin
        asyncio.create_task(self.handle_signalcli_raw_output(self.proc.stdout))
        async for msg in self.signalcli_input_iter():
            logging.info("input to signal: %s", msg)
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
        await self.proc.wait()

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
                logging.info("no signal-cli process")
        await self.datastore.mark_freed()
        await pghelp.close_pools()
        # this doesn't work. see https://github.com/forestcontact/forest-draft/issues/10
        if datastore._memfs_process:
            executor = datastore._memfs_process._get_executor()
            logging.info(executor)
            executor.shutdown(wait=False, cancel_futures=True)
        logging.info("exited".center(60, "="))
        sys.exit(0)
        logging.info(
            "called sys.exit but still running, os.kill sigint to %s",
            os.getpid(),
        )
        os.kill(os.getpid(), signal.SIGINT)
        logging.info("still running after os.kill, trying os._exit")
        os._exit(1)

    async def handle_signalcli_raw_output(
        self,
        stream: asyncio.StreamReader,
    ) -> None:
        while True:
            line = (await stream.readline()).decode().strip()
            if not line:
                break
            await self.handle_signalcli_raw_line(line)

    async def handle_signalcli_raw_line(self, line: str) -> None:
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
        msg = Message(blob)
        if msg.full_text:
            logging.info("signal: %s", line)
        await self.signalcli_output_queue.put(msg)
        return


class Bot(Signal):
    def __init__(self, *args: str) -> None:
        """Creates AND STARTS a bot that routes commands to do_x handlers"""
        self.client_session = aiohttp.ClientSession()
        super().__init__(*args)
        asyncio.create_task(self.start_process())
        asyncio.create_task(self.handle_messages())

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


async def noGet(request: web.Request) -> web.Response:
    raise web.HTTPFound(location="https://signal.org/")


async def send_message_handler(request: web.Request) -> web.Response:
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
        web.get("/", noGet),
        web.post("/user/{phonenumber}", send_message_handler),
    ]
)

if not utils.get_secret("NO_MEMFS"):
    app.on_startup.append(datastore.start_memfs)
    app.on_startup.append(datastore.start_memfs_monitor)


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = bot = Bot()

    web.run_app(app, port=8080, host="0.0.0.0")
