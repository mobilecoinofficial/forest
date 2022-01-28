#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

"""
The core chatbot framework: Message, Signal, Bot, and app
"""
import ast
import asyncio
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import base64
import datetime
import json
import logging
import os
import signal
import sys
import time
import traceback
import urllib
import uuid
import glob

from asyncio import Queue, StreamReader, StreamWriter
from asyncio.subprocess import PIPE
from decimal import Decimal
from functools import wraps
from textwrap import dedent
from typing import Any, AsyncIterator, Callable, Optional, Type, Union

import aiohttp
import termcolor
from aiohttp import web
from phonenumbers import NumberParseException
from prometheus_async import aio
from prometheus_client import Histogram, Summary
from ulid2 import generate_ulid_as_base32 as get_uid

# framework
import mc_util
from forest import autosave, datastore, payments_monitor, pghelp, utils, string_dist
from forest.message import AuxinMessage, Message, StdioMessage

JSON = dict[str, Any]
Response = Union[str, list, dict[str, str], None]

roundtrip_histogram = Histogram("roundtrip_h", "Roundtrip message response time")  # type: ignore
roundtrip_summary = Summary("roundtrip_s", "Roundtrip message response time")

MessageParser = AuxinMessage if utils.AUXIN else StdioMessage
logging.info("Using message parser: %s", MessageParser)
fee_pmob = int(1e12 * 0.0004)


def rpc(
    method: str, param_dict: Optional[dict] = None, _id: str = "1", **params: Any
) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "id": _id,
        "params": (param_dict or {}) | params,
    }


def fmt_ms(ts: int) -> str:
    return datetime.datetime.utcfromtimestamp(ts / 1000).isoformat()


class Signal:
    """
    Represents a auxin-cli session.
    Lifecycle: Downloads the datastore, runs and restarts auxin-cli,
    tries to gracefully kill auxin-cli and upload before exiting.
    I/O: reads auxin-cli's output into auxincli_output_queue,
    has methods for sending commands to auxin-cli, and
    actually writes those json blobs to auxin-cli's stdin.
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
        self.auxincli_output_queue: Queue[Message] = Queue()
        self.auxincli_input_queue: Queue[dict] = Queue()
        self.exiting = False
        self.start_time = time.time()

    async def start_process(self) -> None:
        """
        Add SIGINT handlers. Download datastore. Maybe set profile.
        (Re)start auxin-cli and launch reading and writing with it.
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
        RESTART_TIME = 2  # move somewhere else maybe
        restart_count = 0
        while self.sigints == 0 and not self.exiting:
            path = (
                utils.get_secret("SIGNAL_CLI_PATH")
                or f"{utils.ROOT_DIR}/{'auxin' if utils.AUXIN else 'signal'}-cli"
            )
            if utils.AUXIN:
                path += " --download-path /tmp"
            else:
                path += " --trust-new-identities always"
            command = f"{path} --config {utils.ROOT_DIR} --user {self.bot_number} jsonRpc".split()
            logging.info(command)
            proc_launch_time = time.time()
            self.proc = await asyncio.create_subprocess_exec(
                *command, stdin=PIPE, stdout=PIPE
            )
            logging.info(
                "started auxin-cli @ %s with PID %s",
                self.bot_number,
                self.proc.pid,
            )
            assert self.proc.stdout and self.proc.stdin
            asyncio.create_task(self.handle_auxincli_raw_output(self.proc.stdout))
            # prevent the previous auxin-cli's write task from stealing commands from the input queue
            if write_task:
                write_task.cancel()
            write_task = asyncio.create_task(self.write_commands(self.proc.stdin))
            returncode = await self.proc.wait()
            proc_exit_time = time.time()
            runtime = proc_exit_time - proc_launch_time
            if runtime < RESTART_TIME:
                logging.info("sleeping briefly")
                await asyncio.sleep(RESTART_TIME ** restart_count)
            logging.warning("auxin-cli exited: %s", returncode)
            if returncode == 0:
                logging.info("auxin-cli apparently exited cleanly, not restarting")
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
        """Upload our datastore, close postgres connections pools, kill auxin-cli, exit"""
        logging.info("starting async_shutdown")
        # if we're downloading, then we upload too
        if utils.UPLOAD:
            await self.datastore.upload()
        # ideally also cancel Bot.restart_task
        if self.proc:
            try:
                self.proc.kill()
                if wait and utils.UPLOAD:
                    await self.proc.wait()
                    await self.datastore.upload()
            except ProcessLookupError:
                logging.info("no auxin-cli process")
        if utils.UPLOAD:
            await self.datastore.mark_freed()
        await pghelp.close_pools()
        # this still deadlocks. see https://github.com/forestcontact/forest-draft/issues/10
        if autosave._memfs_process:
            executor = autosave._memfs_process._get_executor()
            logging.info(executor)
            executor.shutdown(wait=False, cancel_futures=True)
        logging.info("exited".center(60, "="))
        sys.exit(0)  # equivelent to `raise SystemExit()`
        logging.info("called sys.exit but still running, trying os._exit")
        # call C fn _exit() without calling cleanup handlers, flushing stdio buffers, etc.
        os._exit(1)

    async def handle_auxincli_raw_output(self, stream: StreamReader) -> None:
        """Read auxin-cli output but delegate handling it"""
        while True:
            line = (await stream.readline()).decode().strip()
            if not line:
                break
            await self.handle_auxincli_raw_line(line)
        logging.info("stopped reading auxin-cli stdout")

    async def enqueue_blob_messages(self, blob: JSON) -> None:
        message_blob: Optional[JSON] = None
        logging.info(blob)
        if "params" in blob:
            if isinstance(blob["params"], list):
                for msg in blob["params"]:
                    if not blob.get("content", {}).get("receipt_message", {}):
                        await self.auxincli_output_queue.put(MessageParser(msg))
            message_blob = blob["params"]
        if "result" in blob:
            if isinstance(blob["result"], list):
                # idt this happens anymore, remove?
                logging.info("results list code path")
                for msg in blob["result"]:
                    if not blob.get("content", {}).get("receipt_message", {}):
                        await self.auxincli_output_queue.put(MessageParser(msg))
            elif isinstance(blob["result"], dict):
                message_blob = blob
            else:
                logging.warning(blob["result"])
        if "error" in blob:
            message_blob = blob
        if message_blob:
            return await self.auxincli_output_queue.put(MessageParser(message_blob))

    async def handle_auxincli_raw_line(self, line: str) -> None:
        if '{"jsonrpc":"2.0","result":[],"id":"receive"}' not in line:
            pass  # logging.debug("auxin: %s", line)
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            logging.info("auxin: %s", line)
            return
        if "error" in blob:
            logging.info("auxin: %s", line)
            error = json.dumps(blob["error"])
            logging.error(
                json.dumps(blob).replace(error, termcolor.colored(error, "red"))
            )
            if "traceback" in blob:
                exception, *tb = blob["traceback"].split("\n")
                logging.error(termcolor.colored(exception, "red"))
                # maybe also send this to admin as a signal message
                for _line in tb:
                    logging.error(_line)
        # {"jsonrpc":"2.0","method":"receive","params":{"envelope":{"source":"+16176088864","sourceNumber":"+16176088864","sourceUuid":"412e180d-c500-4c60-b370-14f6693d8ea7","sourceName":"sylv","sourceDevice":3,"timestamp":1637290344242,"dataMessage":{"timestamp":1637290344242,"message":"/ping","expiresInSeconds":0,"viewOnce":false}},"account":"+447927948360"}}
        try:
            await self.enqueue_blob_messages(blob)
        except KeyError:
            logging.info("auxin parse error: %s", line)
            traceback.print_exception(*sys.exc_info())
        return

    # i'm tempted to refactor these into handle_messages
    async def auxincli_output_iter(self) -> AsyncIterator[Message]:
        """Provides an asynchronous iterator over messages on the queue.
        See Bot for how messages and consumed and dispatched"""
        while True:
            message = await self.auxincli_output_queue.get()
            yield message

    # In the next section, we see how the input queue is populated and consumed

    pending_requests: dict[str, asyncio.Future[Message]] = {}
    pending_messages_sent: dict[str, dict] = {}

    async def wait_resp(
        self, req: Optional[dict] = None, future_key: str = ""
    ) -> Message:
        if req:
            future_key = req["method"] + "-" + get_uid()
            logging.info("expecting response id: %s", future_key)
            req["id"] = future_key
            self.pending_requests[future_key] = asyncio.Future()
            self.pending_messages_sent[future_key] = req
            await self.auxincli_input_queue.put(req)
        # when the result is received, the future will be set
        response = await self.pending_requests[future_key]
        self.pending_requests.pop(future_key)
        return response

    async def auxin_req(self, method: str, **params: Any) -> Message:
        return await self.wait_resp(req=rpc(method, **params))

    async def set_profile(self) -> None:
        """Set signal profile. Note that this will overwrite any mobilecoin address"""
        env = utils.get_secret("ENV")
        # maybe use rpc format
        profile = {
            "command": "updateProfile",
            "given-name": "localbot" if utils.LOCAL else "forestbot",
            "family-name": "" if env == "prod" else env,  # maybe not?
            "avatar": "avatar.png",
        }
        await self.auxincli_input_queue.put(profile)

    async def set_profile_auxin(
        self,
        given_name: Optional[str] = "",
        family_name: Optional[str] = "",
        payment_address: Optional[str] = "",
        profile_path: Optional[str] = None,
    ) -> str:
        params: JSON = {}
        params["name"] = {"givenName": given_name}
        if given_name and family_name:
            params["name"]["familyName"] = family_name
        if payment_address:
            params["mobilecoinAddress"] = payment_address
        if profile_path:
            params["avatarFile"] = profile_path
        future_key = f"setProfile-{get_uid()}"
        await self.auxincli_input_queue.put(rpc("setProfile", params, future_key))
        return future_key

    # this should maybe yield a future (eep) and/or use auxin_req
    async def send_message(  # pylint: disable=too-many-arguments
        self,
        recipient: Optional[str],
        msg: Response,
        group: Optional[str] = None,  # maybe combine this with recipient?
        endsession: bool = False,
        attachments: Optional[list[str]] = None,
        content: str = "",
    ) -> str:
        """
        Builds send command for the specified recipient in jsonrpc format and
        writes to the built command to the underlying signal engine. Supports
        multiple messages.

        Parameters
        -----------
        recipient `Optional[str]`:
            phone number of recepient (if individual user)
        msg `Response`:
            text message to recipient
        group 'Optional[str]':
            group to send message to if specified
        endsession `bool`:
            if specified as True, will reset session key
        attachments 'Optional[list[str]]`
            list of media attachments to upload
        content `str`:
            json string specifying raw message content to be serialized into protobufs
        """
        # Consider inferring desination
        if recipient and group:  # (recipient or group):
            raise ValueError(
                "either a group or individual recipient must be specified, not both; "
                f"got {recipient} and {group}"
            )
        if not recipient and not group:
            raise ValueError(
                f"need either a recipient or a group, got {recipient} and {group}"
            )

        if isinstance(msg, list):
            # return the last stamp
            return [
                await self.send_message(recipient, m, group, endsession, attachments)
                for m in msg
            ][-1]
        if isinstance(msg, dict):
            msg = "\n".join((f"{key}:\t{value}" for key, value in msg.items()))

        params: JSON = {"message": msg}
        if endsession:
            params["end_session"] = True
        if attachments:
            params["attachments"] = attachments
        if content:
            params["content"] = content
        if group:
            if utils.AUXIN:
                logging.error("setting a group message, but auxin doesn't support this")
            params["group-id"] = group
        elif recipient:
            try:
                assert recipient == utils.signal_format(recipient)
            except (AssertionError, NumberParseException):
                try:
                    assert recipient == str(uuid.UUID(recipient))
                except (AssertionError, ValueError) as e:
                    logging.error(
                        "not sending message to invalid recipient %s. error: %s",
                        recipient,
                        e,
                    )
                    return ""
            params["destination" if utils.AUXIN else "recipient"] = str(recipient)
        # maybe use rpc() instead
        future_key = f"send-{get_uid()}"
        json_command: JSON = {
            "jsonrpc": "2.0",
            "id": future_key,
            "method": "send",
            "params": params,
        }
        self.pending_messages_sent[future_key] = json_command
        self.pending_requests[future_key] = asyncio.Future()
        await self.auxincli_input_queue.put(json_command)
        return future_key

    async def admin(self, msg: Response) -> None:
        await self.send_message(utils.get_secret("ADMIN"), msg)

    async def respond(self, target_msg: Message, msg: Response) -> str:
        """Respond to a message depending on whether it's a DM or group"""
        logging.info(target_msg.source)
        if not target_msg.source:
            logging.error(target_msg.blob)
        if not utils.AUXIN and target_msg.group:
            return await self.send_message(None, msg, group=target_msg.group)
        destination = target_msg.source or target_msg.uuid
        return await self.send_message(destination, msg)

    # FIXME: disable for auxin
    async def send_reaction(self, target_msg: Message, emoji: str) -> None:
        """Send a reaction. Protip: you can use e.g. \N{GRINNING FACE} in python"""
        # rip rpc syntax and invalid python variable names
        react = {
            "target-author": target_msg.source,
            "target-timestamp": target_msg.timestamp,
        }
        if target_msg.group:
            react["group"] = target_msg.group
        await self.auxincli_input_queue.put(
            rpc(
                "sendReaction",
                param_dict=react,
                emoji=emoji,
                recipient=target_msg.source,
            )
        )

    # maybe merge with write_commands?
    async def auxincli_input_iter(self) -> AsyncIterator[dict]:
        """Provides an asynchronous iterator over pending auxin-cli commands"""
        while True:
            command = await self.auxincli_input_queue.get()
            yield command

    backoff = False

    messages_until_rate_limit = 50.0
    last_update = time.time()

    def update_and_check_rate_limit(self) -> bool:
        elapsed, self.last_update = (time.time() - self.last_update, time.time())
        rate = 1  # theoretically 1 at least message per second is allowed
        self.messages_until_rate_limit = min(
            self.messages_until_rate_limit + elapsed * rate, 60
        )
        return self.messages_until_rate_limit > 1

    async def write_commands(self, pipe: StreamWriter) -> None:
        """Encode and write pending auxin-cli commands"""
        async for msg in self.auxincli_input_iter():
            if self.backoff:
                logging.info("pausing message writes before retrying")
                await asyncio.sleep(4)
                self.backoff = False
            while not self.update_and_check_rate_limit():
                logging.info(
                    "waiting for rate limit (current: %s)",
                    self.messages_until_rate_limit,
                )
                await asyncio.sleep(1)
            self.messages_until_rate_limit -= 1
            if not msg.get("method"):
                logging.error("msg without method: %s", msg)
            if msg.get("method") != "receive":
                logging.info("input to signal: %s", json.dumps(msg))
            if pipe.is_closing():
                logging.error("auxin-cli stdin pipe is closed")
            pipe.write(json.dumps(msg).encode() + b"\n")
            await pipe.drain()


Datapoint = tuple[int, str, float]  # timestamp in ms, command/info, latency in seconds


def is_admin(msg: Message) -> Optional[bool]:
    return (
        (msg.source == utils.get_secret("ADMIN"))
        or (msg.uuid == utils.get_secret("ADMIN"))
        or (msg.group == utils.get_secret("ADMIN_GROUP"))
        or (msg.source in utils.get_secret("ADMINS").split(","))
        or (msg.uuid and msg.uuid in utils.get_secret("ADMINS"))
    )


def requires_admin(command: Callable) -> Callable:
    @wraps(command)
    async def admin_command(self: "Bot", msg: Message) -> Response:
        if is_admin(msg):
            return await command(self, msg)
        return "you must be an admin to use this command"

    admin_command.admin = True  # type: ignore
    admin_command.hide = True  # type: ignore
    return admin_command


def hide(command: Callable) -> Callable:
    @wraps(command)
    async def hidden_command(self: "Bot", msg: Message) -> Response:
        return await command(self, msg)

    hidden_command.hide = True  # type: ignore
    return hidden_command


class Bot(Signal):
    """Handles messages and command dispatch, as well as basic commands.
    Must be instantiated within a running async loop.
    Subclass this with your own commands.
    """

    def __init__(self, bot_number: Optional[str] = None) -> None:
        """Creates AND STARTS a bot that routes commands to do_x handlers"""
        self.client_session = aiohttp.ClientSession()
        self.mobster = payments_monitor.Mobster()
        self.pongs: dict[str, str] = {}
        self.auxin_roundtrip_latency: list[Datapoint] = []
        self.pending_response_tasks: list[asyncio.Task] = []
        self.commands = [
            name.removeprefix("do_") for name in dir(self) if name.startswith("do_")
        ]
        self.visible_commands = [
            name
            for name in self.commands
            if not hasattr(getattr(self, f"do_{name}"), "hide")
        ]
        super().__init__(bot_number)
        self.restart_task = asyncio.create_task(
            self.start_process()
        )  # maybe cancel on sigint?
        self.queue_task = asyncio.create_task(self.handle_messages())
        if utils.get_secret("MONITOR_WALLET"):
            # currently spams and re-credits the same invoice each reboot
            asyncio.create_task(self.mobster.monitor_wallet())

    async def handle_messages(self) -> None:
        """Read messages from the queue and pass each message to handle_message
        If that returns a non-empty string, send it as a response"""
        async for message in self.auxincli_output_iter():
            if message.id and message.id in self.pending_requests:
                logging.debug("setting result for future %s: %s", message.id, message)
                self.pending_requests[message.id].set_result(message)
                if (
                    message.error
                    and "status: 413" in message.error["data"]
                    and message.id in self.pending_messages_sent
                ):
                    sent_json_message = self.pending_messages_sent.pop(message.id)
                    warn = termcolor.colored(
                        "waiting to retry send after rate limit. message: %s", "red"
                    )
                    logging.warning(warn, sent_json_message)
                    self.backoff = True
                    await asyncio.sleep(4)
                    future_key = f"retry-send-{get_uid()}"
                    self.pending_messages_sent[future_key] = sent_json_message
                    self.pending_requests[future_key] = asyncio.Future()
                    await self.auxincli_input_queue.put(sent_json_message)
                continue
            self.pending_response_tasks = [
                task for task in self.pending_response_tasks if not task.done()
            ] + [asyncio.create_task(self.time_response(message))]

    # maybe this is merged with dispatch_message?
    async def time_response(self, message: Message) -> None:
        future_key = None
        start_time = time.time()
        try:
            response = await self.handle_message(message)
            if response is not None:
                future_key = await self.respond(message, response)
        except:  # pylint: disable=bare-except
            exception_traceback = "".join(traceback.format_exception(*sys.exc_info()))
            # should this actually be parallel?
            self.pending_response_tasks.append(
                asyncio.create_task(self.admin(f"{message}\n{exception_traceback}"))
            )
        python_delta = round(time.time() - start_time, 3)
        note = message.arg0 or ""
        if future_key:
            logging.debug("awaiting future %s", future_key)
            result = await self.wait_resp(future_key=future_key)
            roundtrip_delta = (result.timestamp - message.timestamp) / 1000
            self.auxin_roundtrip_latency.append(
                (message.timestamp, note, roundtrip_delta)
            )
            roundtrip_summary.observe(roundtrip_delta)  # type: ignore
            roundtrip_histogram.observe(roundtrip_delta)  # type: ignore
            logging.info("noted roundtrip time: %s", roundtrip_delta)
            if utils.get_secret("ADMIN_METRICS"):
                await self.admin(
                    f"command: {note}. python delta: {python_delta}s. roundtrip delta: {roundtrip_delta}s",
                )

    def is_command(self, msg: Message) -> bool:
        # "mentions":[{"name":"+447927948360","number":"+447927948360","uuid":"fc4457f0-c683-44fe-b887-fe3907d7762e","start":0,"length":1}
        has_slash = msg.full_text and msg.full_text.startswith("/")
        return has_slash or any(
            mention.get("number") == self.bot_number for mention in msg.mentions
        )

    def match_command(self, msg: Message) -> str:
        if not msg.arg0:
            return ""
        # happy part direct match
        if hasattr(self, "do_" + msg.arg0):
            return msg.arg0
        # always match in dms, only match /commands or @bot in groups
        if utils.get_secret("ENABLE_MAGIC") and (not msg.group or self.is_command(msg)):
            # don't leak admin commands
            valid_commands = self.commands if is_admin(msg) else self.visible_commands
            # closest match
            score, cmd = string_dist.match(msg.arg0, valid_commands)
            if score < (float(utils.get_secret("TYPO_THRESHOLD") or 0.3)):
                return cmd
            # check if there's a unique expansion
            expansions = [
                expanded_cmd
                for expanded_cmd in valid_commands
                if cmd.startswith(msg.arg0)
            ]
            if len(expansions) == 1:
                return expansions[0]
        return ""

    async def handle_message(self, message: Message) -> Response:
        """Method dispatch to do_x commands and goodies.
        Overwrite this to add your own non-command logic,
        but call super().handle_message(message) at the end"""
        # try to get a direct match, or a fuzzy match if appropriate
        if cmd := self.match_command(message):
            # invoke the function and return the response
            return await getattr(self, "do_" + cmd)(message)
        if message.text == "TERMINATE":
            return "signal session reset"
        return await self.default(message)

    def documented_commands(self) -> str:
        commands = ", ".join(
            name.removeprefix("do_")
            for name in dir(self)
            if name.startswith("do_")
            and not hasattr(getattr(self, name), "hide")
            and hasattr(getattr(self, name), "__doc__")
        )
        return f'Documented commands: {commands}\n\nFor more info about a command, try "help" [command]'

    async def default(self, message: Message) -> Response:
        resp = "That didn't look like a valid command!\n" + self.documented_commands()
        # if it messages an echoserver, don't get in a loop (or groups)
        if message.text and not (message.group or message.text == resp):
            return resp
        return None

    async def do_help(self, msg: Message) -> Response:
        """
        help [command]. see the documentation for command, or all commands
        """
        if msg.text and "Documented commands" in msg.text:
            return None
        if msg.arg1:
            try:
                doc = getattr(self, f"do_{msg.arg1}").__doc__
                if doc:
                    if hasattr(getattr(self, f"do_{msg.arg1}"), "hide"):
                        raise AttributeError("Pretend this never happened.")
                    return dedent(doc).strip()
                return f"{msg.arg1} isn't documented, sorry :("
            except AttributeError:
                return f"No such command '{msg.arg1}'"
        else:
            resp = self.documented_commands()
        return resp

    async def do_printerfact(self, _: Message) -> str:
        "Learn a fact about printers"
        async with self.client_session.get(
            utils.get_secret("FACT_SOURCE") or "https://colbyolson.com/printers"
        ) as resp:
            fact = await resp.text()
        return fact.strip()

    @requires_admin
    async def do_eval(self, msg: Message) -> Response:
        """Evaluates a few lines of Python. Preface with "return" to reply with result."""

        async def async_exec(stmts: str, env: Optional[dict]) -> Any:
            parsed_stmts = ast.parse(stmts)
            fn_name = "_async_exec_f"
            my_fn = f"async def {fn_name}(): pass"
            parsed_fn = ast.parse(my_fn)
            for node in parsed_stmts.body:
                ast.increment_lineno(node)
            assert isinstance(parsed_fn.body[0], ast.AsyncFunctionDef)
            # replace the empty async def _async_exec_f(): pass body
            # with the AST parsed from the message
            parsed_fn.body[0].body = parsed_stmts.body
            code = compile(parsed_fn, filename="<ast>", mode="exec")
            exec(code, env or globals())  # pylint: disable=exec-used
            # pylint: disable=eval-used
            return await eval(f"{fn_name}()", env or globals())

        if msg.full_text and len(msg.tokens) > 1:
            source_blob = msg.full_text.replace(msg.arg0, "", 1).lstrip("/ ")
            return str(await async_exec(source_blob, locals()))
        return None

    async def do_ping(self, message: Message) -> str:
        """replies to /ping with /pong"""
        if message.text:
            return f"/pong {message.text}"
        return "/pong"

    @hide
    async def do_uptime(self, _: Message) -> str:
        """Returns a message containing the bot uptime."""
        tot_mins, sec = divmod(int(time.time() - self.start_time), 60)
        hr, mins = divmod(tot_mins, 60)
        t = "Uptime: "
        t += f"{hr}h" if hr else ""
        t += f"{mins}m" if mins else ""
        t += f"{sec}s"
        return t

    @hide
    async def do_pong(self, message: Message) -> str:
        """Stashes the message in context so it's accessible externally."""
        if message.text:
            self.pongs[message.text] = message.text
            return f"OK, stashing {message.text}"
        return "OK"


class PayBot(Bot):
    @requires_admin
    async def do_fsr(self, msg: Message) -> Response:
        """
        Make a request to the Full-Service instance behind the bot. Admin-only.
        ie) /fsr [command] ([arg1] [val1]( [arg2] [val2])...)"""
        if not msg.tokens:
            return "/fsr [command] ([arg1] [val1]( [arg2] [val2]))"
        if len(msg.tokens) == 1:
            return await self.mobster.req(dict(method=msg.tokens[0]))
        if (len(msg.tokens) % 2) == 1:
            fsr_command = msg.tokens[0]
            fsr_keys = msg.tokens[1::2]
            fsr_values = msg.tokens[2::2]
            params = dict(zip(fsr_keys, fsr_values))
            return str(await self.mobster.req_(fsr_command, **params))
        return "/fsr [command] ([arg1] [val1]( [arg2] [val2])...)"

    @requires_admin
    async def do_balance(self, _: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {mc_util.pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"

    async def handle_message(self, message: Message) -> Response:
        if message.payment:
            asyncio.create_task(self.handle_payment(message))
            return None
        return await super().handle_message(message)

    async def get_user_balance(self, account: str) -> float:
        res = await self.mobster.ledger_manager.get_usd_balance(account)
        return float(round(res[0].get("balance"), 2))

    async def handle_payment(self, message: Message) -> None:
        """Decode the receipt, then update balances.
        Blocks on transaction completion, run concurrently"""
        assert message.payment
        logging.info(message.payment)
        amount_pmob = await self.mobster.get_receipt_amount_pmob(
            message.payment["receipt"]
        )
        if amount_pmob is None:
            await self.respond(
                message, "That looked like a payment, but we couldn't parse it"
            )
            return
        amount_mob = float(mc_util.pmob2mob(amount_pmob))
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
        await self.respond(message, await self.payment_response(message, amount_pmob))

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        del msg, amount_pmob  # shush linters
        return "This bot doesn't have a response for payments."

    async def get_address(self, recipient: str) -> Optional[str]:
        result = await self.auxin_req("getPayAddress", peer_name=recipient)
        b64_address = (
            result.blob.get("Address", {}).get("mobileCoinAddress", {}).get("address")
        )
        if result.error or not b64_address:
            logging.info("bad address: %s", result.blob)
            return None
        address = mc_util.b64_public_address_to_b58_wrapper(b64_address)
        return address

    async def do_address(self, msg: Message) -> Response:
        """
        /address
        Returns your MobileCoin address (in standard b58 format.)"""
        address = await self.get_address(msg.source)
        return address or "Sorry, couldn't get your MobileCoin address"

    @requires_admin
    async def do_update(self, msg: Message) -> Response:
        """Renames bot (requires admin) - accepts first name, last name, and address."""
        user_image = None
        if msg.attachments and len(msg.attachments):
            await asyncio.sleep(2)
            attachment_info = msg.attachments[0]
            attachment_path = attachment_info.get("fileName")
            timestamp = attachment_info.get("uploadTimestamp")
            if attachment_path is None:
                attachment_paths = glob.glob(f"/tmp/unnamed_attachment_{timestamp}.*")
                if attachment_paths:
                    user_image = attachment_paths.pop()
            else:
                user_image = f"/tmp/{attachment_path}"
        if user_image or (msg.tokens and len(msg.tokens) > 0):
            await self.set_profile_auxin(
                given_name=msg.arg1,
                family_name=msg.arg2,
                payment_address=msg.arg3,
                profile_path=user_image,
            )
            return "OK"
        return "pass arguments for rename"

    async def mob_request(self, method: str, **params: Any) -> dict:
        """Pass a request through to full-service, but send a message to an admin in case of error"""
        result = await self.mobster.req_(method, **params)
        if "error" in result:
            await self.admin(f"{params}\n{result}")
        return result

    async def fs_receipt_to_payment_message_content(
        self, fs_receipt: dict, note: str = ""
    ) -> str:
        full_service_receipt = fs_receipt["result"]["receiver_receipts"][0]
        # this gets us a Receipt protobuf
        b64_receipt = mc_util.full_service_receipt_to_b64_receipt(full_service_receipt)
        # serde expects bytes to be u8[], not b64
        u8_receipt = [int(char) for char in base64.b64decode(b64_receipt)]
        tx = {"mobileCoin": {"receipt": u8_receipt}}
        note = note or "check out this java-free payment notification"
        payment = {"Item": {"notification": {"note": note, "Transaction": tx}}}
        # SignalServiceMessageContent protobuf represented as JSON (spicy)
        # destination is outside the content so it doesn't matter,
        # but it does contain the bot's profileKey
        resp = await self.auxin_req(
            "send", simulate=True, message="", destination="+15555555555"
        )
        content_skeletor = json.loads(resp.blob["simulate_output"])
        content_skeletor["dataMessage"]["body"] = None
        content_skeletor["dataMessage"]["payment"] = payment
        return json.dumps(content_skeletor)

    async def build_gift_code(self, amount_pmob: int) -> list[str]:
        """Builds a gift code and returns a list of messages to send, given an amount in pMOB."""
        raw_prop = await self.mob_request(
            "build_gift_code",
            account_id=await self.mobster.get_account(),
            value_pmob=str(int(amount_pmob)),
            fee=str(fee_pmob),
            memo="Gift code built with MOBot!",
        )
        prop = raw_prop["result"]["tx_proposal"]
        b58_code = raw_prop["result"]["gift_code_b58"]
        submitted = await self.mob_request(
            "submit_gift_code",
            tx_proposal=prop,
            gift_code_b58=b58_code,
            from_account_id=await self.mobster.get_account(),
        )
        b58 = submitted.get("result", {}).get("gift_code", {}).get("gift_code_b58")
        return [
            "Built Gift Code",
            b58,
            f"redeemable for {str(mc_util.pmob2mob(amount_pmob-fee_pmob)).rstrip('0')} MOB",
        ]

    # FIXME: clarify signature and return details/docs
    async def send_payment(  # pylint: disable=too-many-locals
        self,
        recipient: str,
        amount_pmob: int,
        receipt_message: str = "Transaction sent!",
        confirm_tx_timeout: int = 0,
        **params: Any,
    ) -> Optional[Message]:
        """
        If confirm_tx_timeout is not 0, we wait that many seconds for the tx
        to complete before sending receipt_message to receipient
        params are pasted to the full-service build_transaction call.
        some useful params are comment and input_txo_ids
        """
        address = await self.get_address(recipient)
        account_id = await self.mobster.get_account()
        if not address:
            await self.send_message(
                recipient,
                "Sorry, couldn't get your MobileCoin address. Please make sure you have payments enabled, and have messaged me from your phone!",
            )
            return None
        # TODO: add explicit utxo handling
        raw_prop = await self.mob_request(
            "build_transaction",
            account_id=account_id,
            recipient_public_address=address,
            value_pmob=str(int(amount_pmob)),
            fee=str(int(1e12 * 0.0004)),
            **params,
        )
        prop = raw_prop.get("result", {}).get("tx_proposal")
        tx_id = raw_prop.get("result", {}).get("transaction_log_id")
        # this is to NOT log transactions into the full service DB if the sender
        # wants it private.
        if confirm_tx_timeout:
            # putting the account_id into the request logs it to full service,
            tx_result = await self.mob_request(
                "submit_transaction",
                tx_proposal=prop,
                comment=params.get("comment", ""),
                account_id=account_id,
            )

        elif not prop or not tx_id:
            tx_result = None
        else:
            # if you omit account_id, tx doesn't get logged. Good for privacy,
            # but transactions can't be confirmed by the sending party (you)!
            tx_result = await self.mob_request("submit_transaction", tx_proposal=prop)

        if not isinstance(tx_result, dict) or not tx_result.get("result"):
            # avoid sending tx receipt if there's a tx submission error
            # and send error message back to tx sender
            logging.warning("tx submit error for tx_id: %s", tx_id)
            msg = MessageParser({})
            msg.status, msg.transaction_log_id = "tx_status_failed", tx_id
            return msg

        receipt_resp = await self.mob_request(
            "create_receiver_receipts",
            tx_proposal=prop,
            account_id=await self.mobster.get_account(),
        )
        content = await self.fs_receipt_to_payment_message_content(
            receipt_resp, receipt_message
        )
        # pass our beautifully composed spicy JSON content to auxin.
        # message body is ignored in this case.
        payment_notif = await self.send_message(recipient, "", content=content)
        resp_fut = asyncio.create_task(self.wait_resp(future_key=payment_notif))

        if confirm_tx_timeout:
            logging.debug("Attempting to confirm tx status for %s", recipient)
            status = "tx_status_pending"
            for i in range(confirm_tx_timeout):
                tx_status = await self.mob_request(
                    "get_transaction_log", transaction_log_id=tx_id
                )
                status = (
                    tx_status.get("result", {}).get("transaction_log", {}).get("status")
                )
                if status == "tx_status_succeeded":
                    logging.info(
                        "Tx to %s suceeded - tx data: %s",
                        recipient,
                        tx_status.get("result"),
                    )
                    if receipt_message:
                        await self.send_message(recipient, receipt_message)
                    break
                if status == "tx_status_failed":
                    logging.warning(
                        "Tx to %s failed - tx data: %s",
                        recipient,
                        tx_status.get("result"),
                    )
                    break
                await asyncio.sleep(1)

            if status == "tx_status_pending":
                logging.warning(
                    "Tx to %s timed out - tx data: %s",
                    recipient,
                    tx_status.get("result"),
                )
            resp = await resp_fut
            # the calling function can use these to check the payment status
            resp.status, resp.transaction_log_id = status, tx_id  # type: ignore
            return resp

        return await resp_fut


class QuestionBot(PayBot):
    def __init__(self, bot_number: Optional[str] = None) -> None:
        self.pending_confirmations: dict[str, asyncio.Future[bool]] = {}
        self.pending_answers: dict[str, asyncio.Future[Message]] = {}
        super().__init__(bot_number)

    async def handle_message(self, message: Message) -> Response:
        if message.full_text:
            probably_future = None
            if message.uuid in self.pending_answers:
                probably_future = self.pending_answers[message.uuid]
            if message.source in self.pending_answers:
                probably_future = self.pending_answers[message.uuid]
            if probably_future:
                probably_future.set_result(message)
                return None
        return await super().handle_message(message)

    @hide
    async def do_yes(self, msg: Message) -> Response:
        """Handles 'yes' in response to a pending_confirmation."""
        if (
            msg.uuid not in self.pending_confirmations
            and msg.source not in self.pending_confirmations
        ):
            return "Did I ask you a question?"
        question = None
        if msg.uuid and msg.uuid in self.pending_confirmations:
            question = self.pending_confirmations[msg.uuid]
        if msg.source and msg.source in self.pending_confirmations:
            question = self.pending_confirmations[msg.source]
        if question:
            question.set_result(True)
        return None

    @hide
    async def do_no(self, msg: Message) -> Response:
        """Handles 'no' in response to a pending_confirmation."""
        if (
            msg.uuid not in self.pending_confirmations
            and msg.source not in self.pending_confirmations
        ):
            return "Did I ask you a question?"
        question = None
        if msg.uuid and msg.uuid in self.pending_confirmations:
            question = self.pending_confirmations[msg.uuid]
        if msg.source and msg.source in self.pending_confirmations:
            question = self.pending_confirmations[msg.source]
        if question:
            question.set_result(False)
        return None

    async def ask_freeform_question(
        self, recipient: str, question_text: str = "What's your favourite colour?"
    ) -> str:
        await self.send_message(recipient, question_text)
        answer_future = self.pending_answers[recipient] = asyncio.Future()
        answer = await answer_future
        self.pending_answers.pop(recipient)
        return answer.full_text or ""

    async def ask_yesno_question(
        self, recipient: str, question_text: str = "Are you sure? yes/no"
    ) -> bool:
        self.pending_confirmations[recipient] = asyncio.Future()
        await self.send_message(recipient, question_text)
        result = await self.pending_confirmations[recipient]
        self.pending_confirmations.pop(recipient)
        return result


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
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    msg_data = await request.text()
    rpc_id = await bot.send_message(
        account, msg_data, endsession=request.query.get("endsession")
    )
    resp = await bot.wait_resp(future_key=rpc_id)
    return web.json_response({"status": "sent", "sent_ts": resp.timestamp})


async def admin_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    arg = urllib.parse.unquote(request.query.get("message", "")).strip()
    data = (await request.text()).strip()
    if arg.strip() and data.strip():
        msg = f"{arg}\n{data}"
    else:
        msg = arg or data
    await bot.admin(msg)
    return web.Response(text="OK")


async def metrics(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    return web.Response(
        status=200,
        text="start_time, command, delta\n"
        + "\n".join(
            f"{fmt_ms(t)}, {cmd}, {delta}"
            for t, cmd, delta in bot.auxin_roundtrip_latency
        ),
    )


app = web.Application()


async def add_tiprat(_app: web.Application) -> None:
    async def tiprat(request: web.Request) -> web.Response:
        raise web.HTTPFound("https://tiprat.fly.dev", headers=None, reason=None)

    _app.add_routes([web.route("*", "/{tail:.*}", tiprat)])


app.add_routes(
    [
        web.get("/", no_get),
        web.get("/pongs/{pong}", pong_handler),
        web.post("/user/{phonenumber}", send_message_handler),
        web.post("/admin", admin_handler),
        web.get("/metrics", aio.web.server_stats),
        web.get("/csv_metrics", metrics),
    ]
)

# order of operations:
# 1. start memfs
# 2. instanciate Bot, which may call setup_tmpdir
# 3. download
# 4. start process

app.on_startup.append(add_tiprat)
if utils.MEMFS:
    app.on_startup.append(autosave.start_memfs)
    app.on_startup.append(autosave.start_memfs_monitor)


def run_bot(bot: Type[Bot], local_app: web.Application = app) -> None:
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = bot()

    local_app.on_startup.append(start_wrapper)
    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)


if __name__ == "__main__":
    run_bot(QuestionBot)
