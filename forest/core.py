#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
"""
The core chatbot framework: Message, Signal, Bot, PayBot, and app
"""
import ast
import asyncio
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import base64
import codecs
import datetime
import glob
import json
import logging
import os
import re
import secrets
import signal
import string
import sys
import time
import traceback
import urllib
import uuid
from asyncio import Queue, StreamReader, StreamWriter
from asyncio.subprocess import PIPE
from decimal import Decimal
from functools import wraps
from pathlib import Path
from textwrap import dedent
from typing import (
    Any,
    Callable,
    Coroutine,
    Mapping,
    Optional,
    Type,
    TypeVar,
    Union,
)
import aiohttp
import asyncpg
import termcolor
from aiohttp import web
from phonenumbers import NumberParseException
from prometheus_async import aio
from prometheus_client import Histogram, Summary
from ulid2 import generate_ulid_as_base32 as get_uid

# framework
import mc_util
from forest import autosave, datastore, payments_monitor, pghelp, string_dist, utils
from forest.cryptography import hash_salt
from forest.message import AuxinMessage, Message, Reaction, StdioMessage

try:
    import captcha
except ImportError:
    captcha = None  # type: ignore

JSON = dict[str, Any]
Response = Union[str, list, dict[str, str], None]
AsyncFunc = Callable[..., Coroutine[Any, Any, Any]]
Command = Callable[["Bot", Message], Coroutine[Any, Any, Response]]

roundtrip_histogram = Histogram("roundtrip_h", "Roundtrip message response time")
roundtrip_summary = Summary("roundtrip_s", "Roundtrip message response time")

MessageParser = AuxinMessage if utils.AUXIN else StdioMessage
logging.info("Using message parser: %s", MessageParser)
FEE_PMOB = int(1e12 * 0.0004)


def rpc(
    method: str, param_dict: Optional[dict] = None, _id: str = "1", **params: Any
) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "id": _id,
        "params": (param_dict or {}) | params,
    }


def check_valid_recipient(recipient: str) -> bool:
    try:
        assert recipient == utils.signal_format(recipient)
    except (AssertionError, NumberParseException):
        try:
            assert recipient == str(uuid.UUID(recipient))
        except (AssertionError, ValueError):
            return False
    return True


async def get_attachment_paths(message: Message) -> list[str]:
    if not utils.AUXIN:
        return [
            str(Path("./attachments") / attachment["id"])
            for attachment in message.attachments
        ]
    attachments = []
    for attachment_info in message.attachments:
        attachment_name = attachment_info.get("fileName")
        timestamp = attachment_info.get("uploadTimestamp")
        for i in range(30):  # wait up to 3s
            if attachment_name is None:
                maybe_paths = glob.glob(f"/tmp/unnamed_attachment_{timestamp}.*")
                attachment_path = maybe_paths[0] if maybe_paths else ""
            else:
                attachment_path = f"/tmp/{attachment_name}"
            if attachment_path and Path(attachment_path).exists():
                attachments.append(attachment_path)
                break
            await asyncio.sleep(0.1)
    return attachments


ActivityQueries = pghelp.PGExpressions(
    table="user_activity",
    create_table="""CREATE TABLE user_activity (
        id SERIAL PRIMARY KEY,
        account TEXT,
        first_seen TIMESTAMP default now(),
        last_seen TIMESTAMP default now(),
        bot TEXT,
        UNIQUE (account, bot));""",
    log="""INSERT INTO user_activity (account, bot) VALUES ($1, $2)
    ON CONFLICT ON CONSTRAINT user_activity_account_bot_key1 DO UPDATE SET last_seen=now()""",
)

# This software is intended to promote growth. Like a well managed forest, it grows, it nurtures, it kills.
# Attempts to use this software in a destructive matter, or attempts to harm the forest will be thwarted.
########################################°–_⛤_–°#########################################################


class Signal:
    """
    Represents a signal-cli/auxin-cli session.
    Lifecycle: Downloads the datastore, runs and restarts signal client,
    tries to gracefully kill signal and upload before exiting.
    I/O: reads signal client's output into inbox,
    has methods for sending commands to the signal client, and
    actually writes those json blobs to signal client's stdin.
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
        self.inbox: Queue[Message] = Queue()
        self.outbox: Queue[dict] = Queue()
        self.exiting = False
        self.start_time = time.time()
        self.sent_messages: dict[int, JSON] = {}

    async def start_process(self) -> None:
        """
        Add SIGINT handlers. Download datastore.
        (Re)start signal client and launch reading and writing with it.
        """
        # things that don't work: loop.add_signal_handler(async_shutdown) - TypeError
        # signal.signal(sync_signal_handler) - can't interact with loop
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, self.sync_signal_handler)
        logging.debug("added signal handler, downloading...")
        if utils.DOWNLOAD:
            await self.datastore.download()
        write_task: Optional[asyncio.Task] = None
        restart_count = 0
        max_backoff = 15
        while self.sigints == 0 and not self.exiting:
            path = utils.SIGNAL_PATH
            if utils.AUXIN:
                path += " --download-path /tmp"
            else:
                path += " --trust-new-identities always"
            command = f"{path} --config {utils.ROOT_DIR} --user {self.bot_number} jsonRpc".split()
            logging.info(command)
            proc_launch_time = time.time()
            # this ought to FileNotFoundError but doesn't
            self.proc = await asyncio.create_subprocess_exec(
                *command, stdin=PIPE, stdout=PIPE
            )
            logging.info(
                "started %s @ %s with PID %s",
                utils.SIGNAL,
                self.bot_number,
                self.proc.pid,
            )
            assert self.proc.stdout and self.proc.stdin
            asyncio.create_task(self.read_signal_stdout(self.proc.stdout))
            # prevent the previous signal client's write task from stealing commands from the outbox queue
            if write_task:
                write_task.cancel()
            write_task = asyncio.create_task(self.write_commands(self.proc.stdin))
            returncode = await self.proc.wait()
            proc_exit_time = time.time()
            runtime = proc_exit_time - proc_launch_time
            if runtime > max_backoff * 4:
                restart_count = 0
            restart_count += 1
            backoff = 0.5 * (2**restart_count - 1)
            logging.warning("Signal exited with returncode %s", returncode)
            if backoff > max_backoff:
                logging.info(
                    "%s exiting after %s retries", self.bot_number, restart_count
                )
                break
            logging.info("%s will restart in %s second(s)", self.bot_number, backoff)
            await asyncio.sleep(backoff)

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
        """
        Upload our datastore, close postgres connections pools, kill signal, kill autosave, exit
        """
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
                logging.info(f"no {utils.SIGNAL} process")
        if utils.UPLOAD:
            await self.datastore.mark_freed()
        await pghelp.pool.close()
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

    def log_task_result(
        self,
        task: asyncio.Task,
    ) -> None:
        """
        Done callback which logs task done result
        args:
            task (asyncio.task): Finished task
        """
        name = task.get_name() + "-" + getattr(task.get_coro(), "__name__", "")
        try:
            result = task.result()
            logging.info("final result of %s was %s", name, result)
        except asyncio.CancelledError:
            logging.info("task %s was cancelled", name)
        except Exception:  # pylint: disable=broad-except
            logging.exception("%s errored", name)

    def restart_task_callback(
        self,
        _func: AsyncFunc,
    ) -> Callable:
        def handler(task: asyncio.Task) -> None:
            name = task.get_name() + "-" + getattr(task.get_coro(), "__name__", "")
            if self.sigints > 1:
                return
            if asyncio.iscoroutinefunction(_func):
                task = asyncio.create_task(_func())
                task.add_done_callback(self.restart_task_callback(_func))
                logging.info("%s restarting", name)

        return handler

    async def read_signal_stdout(self, stream: StreamReader) -> None:
        """Read auxin-cli/signal-cli output but delegate handling it"""
        while True:
            line = (await stream.readline()).decode().strip()
            if not line:
                break
            await self.decode_signal_line(line)
        logging.info("stopped reading signal stdout")

    async def decode_signal_line(self, line: str) -> None:
        "decode json and log errors"
        if '{"jsonrpc":"2.0","result":[],"id":"receive"}' not in line:
            pass  # logging.debug("signal: %s", line)
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            logging.info("signal: %s", line)
            return
        if "error" in blob:
            logging.info("signal: %s", line)
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
            if "sender keys" in blob["error"] and self.proc:
                logging.error("killing signal-cli")
                self.proc.kill()
                return
        # {"jsonrpc":"2.0","method":"receive","params":{"envelope":{"source":"+16176088864","sourceNumber":"+16176088864","sourceUuid":"412e180d-c500-4c60-b370-14f6693d8ea7","sourceName":"sylv","sourceDevice":3,"timestamp":1637290344242,"dataMessage":{"timestamp":1637290344242,"message":"/ping","expiresInSeconds":0,"viewOnce":false}},"account":"+447927948360"}}
        try:
            await self.enqueue_blob_messages(blob)
        except KeyError:
            logging.info("signal parse error: %s", line)
            traceback.print_exception(*sys.exc_info())
        return

    async def enqueue_blob_messages(self, blob: JSON) -> None:
        "turn rpc blobs into the appropriate number of Messages and put them in the inbox"
        message_blob: Optional[JSON] = None
        if blob.get("id") != "PONG":
            logging.info(json.dumps(blob))
        if "params" in blob:
            if isinstance(blob["params"], list):
                for msg in blob["params"]:
                    if not blob.get("content", {}).get("receipt_message", {}):
                        await self.inbox.put(MessageParser(msg))
            message_blob = blob["params"]
        if "result" in blob:
            if isinstance(blob["result"], dict):
                message_blob = blob
            else:
                logging.warning(blob["result"])
        if "error" in blob:
            message_blob = blob
        if message_blob:
            return await self.inbox.put(MessageParser(message_blob))

    # In the next section, we see how the outbox queue is populated and consumed

    pending_requests: dict[str, asyncio.Future[Message]] = {}
    pending_messages_sent: dict[str, dict] = {}

    async def wait_for_response(
        self, req: Optional[dict] = None, rpc_id: str = ""
    ) -> Message:
        """
        if a req is given, put in the outbox with along with a future for its result.
        if an rpc_id or req was given, wait for that future and return the result from
        auxin-cli/signal-cli
        """
        if req:
            rpc_id = req["method"] + "-" + get_uid()
            logging.info("expecting response id: %s", rpc_id)
            req["id"] = rpc_id
            self.pending_requests[rpc_id] = asyncio.Future()
            self.pending_messages_sent[rpc_id] = req
            await self.outbox.put(req)
        # when the result is received, the future will be set
        response = await self.pending_requests[rpc_id]
        self.pending_requests.pop(rpc_id)
        return response

    async def signal_rpc_request(self, method: str, **params: Any) -> Message:
        """Sends a jsonRpc command to signal-cli or auxin-cli"""
        return await self.wait_for_response(req=rpc(method, **params))

    async def set_profile_auxin(
        self,
        given_name: Optional[str] = "",
        family_name: Optional[str] = "",
        payment_address: Optional[str] = "",
        profile_path: Optional[str] = None,
        **kwargs: Optional[str],
    ) -> str:
        """set given and family name, payment address (must be b64 format),
        and profile picture"""
        params: JSON = {"name": {"givenName": given_name}}
        if given_name and family_name:
            params["name"]["familyName"] = family_name
        if payment_address:
            params["mobilecoinAddress"] = payment_address
        if profile_path:
            params["avatarFile"] = profile_path
        for parameter, value in kwargs.items():
            if value:
                params[parameter] = value
        rpc_id = f"setProfile-{get_uid()}"
        await self.outbox.put(rpc("setProfile", params, rpc_id))
        return rpc_id

    async def save_sent_message(self, rpc_id: str, params: dict[str, str]) -> None:
        result = await self.pending_requests[rpc_id]
        logging.info("got timestamp %s for blob %s", result.timestamp, params)
        self.sent_messages[result.timestamp] = params
        self.sent_messages[result.timestamp]["reactions"] = {}

    # this should maybe yield a future (eep) and/or use signal_rpc_request
    async def send_message(  # pylint: disable=too-many-arguments
        self,
        recipient: Optional[str],
        msg: Response,
        group: Optional[str] = None,  # maybe combine this with recipient?
        endsession: bool = False,
        attachments: Optional[list[str]] = None,
        content: str = "",
        **other_params: Any,
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
                await self.send_message(
                    recipient, m, group, endsession, attachments, **other_params
                )
                for m in msg
            ][-1]
        if isinstance(msg, dict):
            msg = "\n".join((f"{key}:\t{value}" for key, value in msg.items()))

        params: JSON = {"message": msg, **other_params}
        if endsession:
            params["end_session"] = True
        if attachments:
            params["attachments"] = attachments
        if content:
            params["content"] = content
        if group and not utils.AUXIN:
            params["group-id"] = group
        if recipient and not utils.AUXIN:
            if not check_valid_recipient(recipient):
                logging.error("not sending message to invalid recipient %s", recipient)
                return ""
            params["recipient"] = str(recipient)
        if recipient and utils.AUXIN:
            params["destination"] = str(recipient)
        if group and utils.AUXIN:
            params["group"] = group
        # maybe use rpc() instead
        rpc_id = f"send-{get_uid()}"
        json_command: JSON = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "send",
            "params": params,
        }
        self.pending_messages_sent[rpc_id] = json_command
        self.pending_requests[rpc_id] = asyncio.Future()
        await self.outbox.put(json_command)
        asyncio.create_task(self.save_sent_message(rpc_id, params))
        return rpc_id

    async def admin(self, msg: Response, **other_params: Any) -> None:
        "send a message to admin"
        if group := utils.get_secret("ADMIN_GROUP"):
            await self.send_message(None, msg, group=group, **other_params)
        else:
            await self.send_message(utils.get_secret("ADMIN"), msg, **other_params)

    async def respond(
        self, target_msg: Message, msg: Response, **other_params: Any
    ) -> str:
        """Respond to a message depending on whether it's a DM or group"""
        logging.debug("responding to %s", target_msg.source)
        if not target_msg.source:
            logging.error(json.dumps(target_msg.blob))
        if target_msg.group:
            return await self.send_message(
                None, msg, group=target_msg.group, **other_params
            )
        destination = target_msg.source or target_msg.uuid
        return await self.send_message(destination, msg, **other_params)

    async def send_reaction(self, target_msg: Message, emoji: str) -> None:
        """Send a reaction. Protip: you can use e.g. \N{GRINNING FACE} in python"""
        react = {
            "target-author": target_msg.source,
            "target-timestamp": target_msg.timestamp,
        }
        if target_msg.group:
            react["group"] = target_msg.group
        cmd = rpc(
            "sendReaction",
            param_dict=react,
            emoji=emoji,
            recipient=target_msg.source,
        )
        await self.outbox.put(cmd)

    async def typing_message_content(
        self, stop: bool = False, group_id: str = ""
    ) -> str:
        "serialized typing message content to pass to auxin --content for turning into protobufs"
        resp = await self.signal_rpc_request(
            "send", simulate=True, message="", destination="+15555555555"
        )
        # simulate gives us a dict corresponding to the protobuf structure
        content_skeletor = json.loads(resp.blob["simulate_output"])
        # typingMessage excludes having a dataMessage
        content_skeletor["dataMessage"] = None
        content_skeletor["typingMessage"] = {
            "action": "STOPPED" if stop else "STARTED",
            "timestamp": int(time.time() * 1000),
        }
        if group_id:
            content_skeletor["typingMessage"]["groupId"] = group_id
        return json.dumps(content_skeletor)

    async def send_typing(
        self,
        msg: Optional[Message] = None,
        stop: bool = False,
        recipient: str = "",
        group: str = "",
    ) -> None:
        "Send a typing indicator to the person or group the message is from"
        # typing indicators last 15s on their own
        # https://github.com/signalapp/Signal-Android/blob/master/app/src/main/java/org/thoughtcrime/securesms/components/TypingStatusRepository.java#L32
        if msg:
            group = msg.group or ""
            recipient = msg.source
        if utils.AUXIN:
            if group:
                content = await self.typing_message_content(stop, group)
                await self.send_message(None, "", group=group, content=content)
            else:
                content = await self.typing_message_content(stop)
                await self.send_message(recipient, "", content=content)
            return
        if group:
            await self.outbox.put(rpc("sendTyping", group_id=[group], stop=stop))
        else:
            await self.outbox.put(rpc("sendTyping", recipient=[recipient], stop=stop))

    backoff = False
    messages_until_rate_limit = 1000.0
    last_update = time.time()

    def update_and_check_rate_limit(self) -> bool:
        """Returns whether we think signal server will rate limit us for sending a
        message right now"""
        elapsed, self.last_update = (time.time() - self.last_update, time.time())
        rate = 1  # theoretically 1 at least message per second is allowed
        self.messages_until_rate_limit = min(
            self.messages_until_rate_limit + elapsed * rate, 60
        )
        return self.messages_until_rate_limit > 1

    async def write_commands(self, pipe: StreamWriter) -> None:
        """Encode and write pending auxin-cli/signal-cli commands"""
        while True:
            command = await self.outbox.get()
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
            if not command.get("method"):
                logging.error("command without method: %s", command)
            if command.get("method") != "receive":
                logging.info("input to signal: %s", json.dumps(command))
            if pipe.is_closing():
                logging.error("signal stdin pipe is closed")
            pipe.write(json.dumps(command).encode() + b"\n")
            await pipe.drain()


class UserError(Exception):
    pass


def is_admin(msg: Message) -> bool:
    ADMIN = utils.get_secret("ADMIN") or ""
    ADMIN_GROUP = utils.get_secret("ADMIN_GROUP") or ""
    ADMINS = utils.get_secret("ADMINS") or ""
    source_admin = msg.source and (msg.source in ADMIN or msg.source in ADMINS)
    source_uuid = msg.uuid and (msg.uuid in ADMIN or msg.uuid in ADMINS)
    return source_admin or source_uuid or bool(msg.group and msg.group in ADMIN_GROUP)


B = TypeVar("B", bound="Bot")


def requires_admin(command: AsyncFunc) -> AsyncFunc:
    @wraps(command)
    async def admin_command(self: B, msg: Message) -> Response:
        if is_admin(msg):
            return await command(self, msg)
        return "you must be an admin to use this command"

    admin_command.admin = True  # type: ignore
    admin_command.hide = True  # type: ignore
    return admin_command


def hide(command: AsyncFunc) -> AsyncFunc:
    @wraps(command)
    async def hidden_command(self: B, msg: Message) -> Response:
        return await command(self, msg)

    hidden_command.hide = True  # type: ignore
    return hidden_command


def group_help_text(text: str) -> Callable:
    def decorate(command: Callable) -> Callable:
        @wraps(command)
        async def group_help_text_command(self: "Bot", msg: Message) -> Response:
            return await command(self, msg)

        group_help_text_command.__group_doc__ = text  # type: ignore
        return group_help_text_command

    return decorate


Datapoint = tuple[int, str, float]  # timestamp in ms, command/info, latency in seconds


class Bot(Signal):
    """Handles messages and command dispatch, as well as basic commands.
    Must be instantiated within a running async loop.
    Subclass this with your own commands.
    """

    def __init__(self, bot_number: Optional[str] = None) -> None:
        """Creates AND STARTS a bot that routes commands to do_x handlers"""
        self.client_session = aiohttp.ClientSession()
        self.mobster = payments_monitor.StatefulMobster()
        self.pongs: dict[str, str] = {}
        self.signal_roundtrip_latency: list[Datapoint] = []
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
        self.activity = pghelp.PGInterface(
            query_strings=ActivityQueries, database=utils.get_secret("DATABASE_URL")
        )
        # set of users we've received messages from in the last minute
        self.seen_users: set[str] = set()
        self.log_activity_task = asyncio.create_task(self.log_activity())
        self.log_activity_task.add_done_callback(self.log_task_result)
        self.restart_task = asyncio.create_task(
            self.start_process()
        )  # maybe cancel on sigint?
        self.restart_task.add_done_callback(self.log_task_result)
        self.handle_messages_task = asyncio.create_task(self.handle_messages())
        self.handle_messages_task.add_done_callback(self.log_task_result)
        self.handle_messages_task.add_done_callback(
            self.restart_task_callback(self.handle_messages)
        )

    async def log_activity(self) -> None:
        """
        every 60s, update the user_activity table with users we've seen
        runs in the bg as batches to avoid a seperate db query for every message
        used for signup metrics
        """
        if not pghelp.pool.pool:
            await pghelp.pool.connect(utils.get_secret("DATABASE_URL"), "user_activity")
        while 1:
            await asyncio.sleep(60)
            if not self.seen_users:
                continue
            try:
                async with pghelp.pool.acquire() as conn:
                    # executemany batches this into an atomic db query
                    await conn.executemany(
                        self.activity.queries["log"],
                        [(name, utils.APP_NAME) for name in self.seen_users],
                    )
                    logging.debug("recorded %s seen users", len(self.seen_users))
                    self.seen_users = set()
            except asyncpg.UndefinedTableError:
                logging.info("creating user_activity table")
                await self.activity.create_table()

    async def handle_messages(self) -> None:
        """
        Read messages from the queue. If it matches a pending request to auxin-cli/signal-cli,
        set the result for that request. If said result is being rate limited, retry sending it
        after pausing. Otherwise, concurrently respond to each message.
        """
        metrics_salt = utils.get_secret("METRICS_SALT")
        while True:
            message = await self.inbox.get()
            if metrics_salt and message.uuid:
                self.seen_users.add(hash_salt(message.uuid, metrics_salt))
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
                    rpc_id = f"retry-send-{get_uid()}"
                    self.pending_messages_sent[rpc_id] = sent_json_message
                    self.pending_requests[rpc_id] = asyncio.Future()
                    await self.outbox.put(sent_json_message)
                continue
            self.pending_response_tasks = [
                task for task in self.pending_response_tasks if not task.done()
            ] + [asyncio.create_task(self.respond_and_collect_metrics(message))]

    # maybe this is merged with dispatch_message?
    async def respond_and_collect_metrics(self, message: Message) -> None:
        """
        Pass each message to handle_message. Notify an admin if an error happens.
        If that returns a non-empty string, send it as a reply,
        then record how long this took.
        """
        rpc_id = None
        start_time = time.time()
        try:
            response = await self.handle_message(message)
            if response is not None:
                rpc_id = await self.respond(message, response)
        except UserError as e:
            rpc_id = await self.respond(message, str(e))
        except:  # pylint: disable=bare-except
            exception_traceback = "".join(traceback.format_exception(*sys.exc_info()))
            logging.info("error handling message %s %s", message, exception_traceback)
            self.pending_response_tasks.append(
                asyncio.create_task(self.admin(f"{message}\n{exception_traceback}"))
            )
        python_delta = round(time.time() - start_time, 3)
        note = message.arg0 or ""
        if rpc_id:
            logging.debug("awaiting future %s", rpc_id)
            result = await self.wait_for_response(rpc_id=rpc_id)
            roundtrip_delta = (result.timestamp - message.timestamp) / 1000
            self.signal_roundtrip_latency.append(
                (message.timestamp, note, roundtrip_delta)
            )
            roundtrip_summary.observe(roundtrip_delta)
            roundtrip_histogram.observe(roundtrip_delta)
            logging.info("noted roundtrip time: %s", roundtrip_delta)
            if utils.get_secret("ADMIN_METRICS"):
                await self.admin(
                    f"command: {note}. python delta: {python_delta}s. roundtrip delta: {roundtrip_delta}s",
                )

    async def handle_reaction(self, msg: Message) -> Response:
        """
        route a reaction to the original message.
        #if the number of reactions that message has is a fibonacci number, notify the message's author
        this is probably flakey, because signal only gives us timestamps and
        not message IDs
        """
        assert isinstance(msg.reaction, Reaction)
        react = msg.reaction
        logging.debug("reaction from %s targeting %s", msg.source, react.ts)
        if react.author != self.bot_number or react.ts not in self.sent_messages:
            return None
        self.sent_messages[react.ts]["reactions"][msg.source] = react.emoji
        logging.debug("found target message %s", repr(self.sent_messages[react.ts]))
        return None

    def mentions_us(self, msg: Message) -> bool:
        # "mentions":[{"name":"+447927948360","number":"+447927948360","uuid":"fc4457f0-c683-44fe-b887-fe3907d7762e","start":0,"length":1}
        return any(mention.get("number") == self.bot_number for mention in msg.mentions)

    def is_command(self, msg: Message) -> bool:
        if msg.full_text:
            return msg.full_text.startswith("/") or self.mentions_us(msg)
        return False

    def match_command(self, msg: Message) -> str:
        """return the appropriate command a message is calling for"""
        if not msg.arg0:
            return ""
        # probably wrong
        if self.mentions_us(msg) and msg.full_text:
            msg.parse_text(msg.full_text.lstrip("\N{Object Replacement Character} "))
        # happy part direct match
        if hasattr(self, "do_" + msg.arg0):
            return msg.arg0
        # always match in dms, only match /commands or @bot in groups
        if utils.get_secret("ENABLE_MAGIC") and (not msg.group or self.is_command(msg)):
            logging.debug("running magic")
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
        if message.reaction:
            logging.info("saw a reaction")
            return await self.handle_reaction(message)
        # try to get a direct match, or a fuzzy match if appropriate
        if cmd := self.match_command(message):
            # invoke the function and return the response
            return await getattr(self, "do_" + cmd)(message)
        if message.text == "TERMINATE":
            return "signal session reset"
        return await self.default(message)

    def documented_commands(self) -> str:
        # check for only commands that have docstrings
        commands = ", ".join(
            name.removeprefix("do_")
            for name in dir(self)
            if name.startswith("do_")
            and not hasattr(getattr(self, name), "hide")
            and hasattr(getattr(self, name), "__doc__")
        )
        return f'Documented commands: {commands}\n\nFor more info about a command, try "help" [command]'

    async def default(self, message: Message) -> Response:
        "Default response. Override in your class to change this behavior"
        resp = "That didn't look like a valid command!\n" + self.documented_commands()
        # if it messages an echoserver, don't get in a loop (or groups)
        if message.text and not (
            message.group
            or "Documented commands" in message.text
            or resp == message.text
        ):
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
                cmd = getattr(self, f"do_{msg.arg1}")
                if hasattr(getattr(self, f"do_{msg.arg1}"), "hide"):
                    raise AttributeError("Pretend this never happened.")
                # allow messages to have a different helptext in groups
                if hasattr(cmd, "__group_doc__") and msg.group:
                    return dedent(cmd.__group_doc__).strip()
                doc = cmd.__doc__
                if doc:
                    return dedent(doc).strip()
                return f"{msg.arg1} isn't documented, sorry :("
            except AttributeError:
                return f"No such command '{msg.arg1}'"
        else:
            resp = self.documented_commands()
        return resp

    @requires_admin
    async def do_eval(self, msg: Message) -> Response:
        """Evaluates a few lines of Python. Preface with "return" to reply with result."""

        async def async_exec(stmts: str, env: Optional[dict] = None) -> Any:
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

        if msg.full_text and msg.tokens and len(msg.tokens) > 1:
            source_blob = msg.full_text.replace(msg.arg0, "", 1).lstrip("/ ")
            try:
                return str(await async_exec(source_blob, globals() | locals()))
            except:  # pylint: disable=bare-except
                exception_traceback = "".join(
                    traceback.format_exception(*sys.exc_info())
                )
                return exception_traceback
        return None

    def get_recipients(self) -> list[dict[str, str]]:
        """Returns a list of all known recipients by parsing underlying datastore."""
        return json.loads(
            open(f"data/{self.bot_number}.d/recipients-store").read()
        ).get("recipients", [])

    def get_uuid_by_phone(self, phonenumber: str) -> Optional[str]:
        """Queries the recipients-store file for a UUID, provided a phone number."""
        if phonenumber.startswith("+"):
            maybe_recipient = [
                recipient
                for recipient in self.get_recipients()
                if phonenumber == recipient.get("number")
            ]
            if maybe_recipient:
                return maybe_recipient[0]["uuid"]
        return None

    def get_number_by_uuid(self, uuid_: str) -> Optional[str]:
        """Queries the recipients-store file for a phone number, provided a uuid."""
        if uuid_.count("-") == 4:
            maybe_recipient = [
                recipient
                for recipient in self.get_recipients()
                if uuid_ == recipient.get("uuid")
            ]
            if maybe_recipient:
                return maybe_recipient[0]["number"]
        return None


class ExtrasBot(Bot):
    async def do_printerfact(self, _: Message) -> str:
        "Learn a fact about printers"
        async with self.client_session.get(
            utils.get_secret("FACT_SOURCE") or "https://colbyolson.com/printers"
        ) as resp:
            fact = await resp.text()
        return fact.strip()

    async def do_ping(self, message: Message) -> str:
        """replies to /ping with /pong"""
        if message.text:
            return f"/pong {message.text}"
        return "/pong"

    @hide
    async def do_pong(self, message: Message) -> str:
        """Stashes the message in context so it's accessible externally."""
        if message.arg1 and message.arg2:
            self.pongs[message.arg1] = message.arg2
            return f"OK, stashing {len(message.arg2)} at {message.arg1}"
        if message.text:
            self.pongs[message.text] = message.text
            return f"OK, stashing {message.text}"
        return "OK"

    @hide
    async def do_commit_msg(self, _: Message) -> str:
        try:
            return f"Commit message: {open('COMMIT_EDITMSG').read()}"
        except FileNotFoundError:
            return "No commit message available"

    async def do_signalme(self, _: Message) -> Response:
        """signalme
        Returns a link to share the bot with friends!"""
        return f"https://signal.me/#p/{self.bot_number}"

    @hide
    async def do_rot13(self, msg: Message) -> Response:
        """rot13 encodes the message.
        > rot13 hello world
        uryyb jbeyq"""
        return codecs.encode(msg.text, "rot13")

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


class PayBot(ExtrasBot):
    PAYMENTS_HELPTEXT = """Enable Signal Pay:

    1. In Signal, tap “⬅️“ & tap on your profile icon in the top left & tap *Settings*

    2. Tap *Payments* & tap *Activate Payments*

    For more information on Signal Payments visit:

    https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""

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
    async def do_setup(self, _: Message) -> str:
        if not utils.AUXIN:
            return "Can't set payment address without auxin"
        await self.set_profile_auxin(
            payment_address=mc_util.b58_wrapper_to_b64_public_address(
                await self.mobster.ensure_address()
            )
        )
        return "OK"

    @requires_admin
    async def do_balance(self, _: Message) -> Response:
        """Returns bot balance in MOB."""
        return f"Bot has balance of {mc_util.pmob2mob(await self.mobster.get_balance()).quantize(Decimal('1.0000'))} MOB"

    async def handle_message(self, message: Message) -> Response:
        if message.payment:
            asyncio.create_task(self.handle_payment(message))
            return None
        return await super().handle_message(message)

    async def get_user_usd_balance(self, account: str) -> float:
        res = await self.mobster.ledger_manager.get_usd_balance(account)
        return float(round(res[0].get("balance"), 2))

    async def get_user_pmob_balance(self, account: str) -> int:
        res = await self.mobster.ledger_manager.get_pmob_balance(account)
        return res[0].get("balance")

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
        await self.respond(message, await self.payment_response(message, amount_pmob))

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        """Triggers on successful payment"""
        del msg  # shush linter
        amount_mob = float(mc_util.pmob2mob(amount_pmob))
        amount_usd = round(await self.mobster.pmob2usd(amount_pmob), 2)
        return f"Thank you for sending {float(amount_mob)} MOB ({amount_usd} USD)"

    async def get_signalpay_address(self, recipient: str) -> Optional[str]:
        "get a receipient's mobilecoin address"
        result = await self.signal_rpc_request("getPayAddress", peer_name=recipient)
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
        address = await self.get_signalpay_address(msg.source)
        return address or "Sorry, couldn't get your MobileCoin address"

    @requires_admin
    async def do_set_profile(self, message: Message) -> Response:
        """Renames bot (requires admin) - accepts first name, last name, and payment address."""
        attachments = await get_attachment_paths(message)
        user_image = attachments[0] if attachments else None
        if user_image or (message.tokens and len(message.tokens) > 0):
            await self.set_profile_auxin(
                given_name=message.arg1,
                family_name=message.arg2,
                payment_address=message.arg3,
                profile_path=user_image,
            )
            return "OK"
        return "pass arguments for rename"

    async def mob_request(self, method: str, **params: Any) -> dict:
        """Pass a request through to full-service, but send a message to an admin in case of error"""
        result = await self.mobster.req_(method, **params)
        if "error" in result:
            await self.admin(f"{result}\nReturned by:\n\n{str(params)[:1024]}...")
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
        resp = await self.signal_rpc_request(
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
            fee=str(FEE_PMOB),
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
            f"redeemable for {str(mc_util.pmob2mob(amount_pmob - FEE_PMOB)).rstrip('0')} MOB",
        ]

    # FIXME: clarify signature and return details/docs
    async def send_payment(  # pylint: disable=too-many-locals
        self,
        recipient: str,
        amount_pmob: int,
        receipt_message: str = "Transaction sent!",
        confirm_tx_timeout: int = 60,
        **params: Any,
    ) -> Optional[Message]:
        """
        If confirm_tx_timeout is not 0, we wait that many seconds for the tx
        to complete before sending receipt_message to receipient
        params are pasted to the full-service build_transaction call.
        some useful params are comment and input_txo_ids
        """
        address = await self.get_signalpay_address(recipient)
        account_id = await self.mobster.get_account()
        if not address:
            raise UserError(
                "Sorry, couldn't get your MobileCoin address. Please make sure you have payments enabled, and have messaged me from your phone!"
            )
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
            tx_result: Optional[dict] = await self.mob_request(
                "submit_transaction",
                tx_proposal=prop,
                comment=params.get("comment", ""),
                account_id=account_id,
            )
        elif prop and tx_id:
            # if you omit account_id, tx doesn't get logged. Good for privacy,
            # but transactions can't be confirmed by the sending party (you)!
            tx_result = await self.mob_request("submit_transaction", tx_proposal=prop)
        else:
            tx_result = {"error": {"message": "InternalError"}}
        # {'method': 'submit_transaction', 'error': {'code': -32603, 'message': 'InternalError', 'data': {'server_error': 'Database(Diesel(DatabaseError(__Unknown, "database is locked")))', 'details': 'Error interacting with the database: Diesel Error: database is locked'}}, 'jsonrpc': '2.0', 'id': 1}
        if not tx_result or (
            tx_result.get("error")
            and "InternalError" in tx_result.get("error", {}).get("message", "")
        ):
            return None
            # logging.info("InternalError occurred, retrying in 60s")
            # await asyncio.sleep(1)
            # tx_result = await self.mob_request("submit_transaction", tx_proposal=prop)
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
        resp_future = asyncio.create_task(self.wait_for_response(rpc_id=payment_notif))

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
            resp = await resp_future
            # the calling function can use these to check the payment status
            resp.status, resp.transaction_log_id = status, tx_id  # type: ignore
            return resp
        return await resp_future


# we should just have either a hasable user type or a mapping subtype

V = TypeVar("V")


def get_source_or_uuid_from_dict(
    msg: Message, dict_: Union[Mapping[str, V], Mapping[tuple[str, str], V]]
) -> tuple[bool, Optional[V]]:
    """A common pattern is to store intermediate state for individual users as a dictionary.
    Users can be referred to by some combination of source (a phone number) or uuid (underlying user ID)
    This abstracts over the possibility space, returning a boolean indicator of whether the sender of a Message
    is referenced in a dict, and the value pointed at (if any)."""
    group = msg.group or ""
    for key in [(msg.source, group), (msg.uuid, group), msg.source, msg.uuid]:
        if value := dict_.get(key):  # type: ignore
            return True, value
    return False, None


def is_first_device(msg: Message) -> bool:
    if not msg or not msg.blob:
        return False
    return msg.blob.get("remote_address", {}).get("device_id", 0) == 1


class QuestionBot(PayBot):
    """Class of Bots that have methods for asking questions and awaiting answers"""

    def __init__(self, bot_number: Optional[str] = None) -> None:
        self.pending_answers: dict[tuple[str, str], asyncio.Future[Message]] = {}
        self.requires_first_device: dict[str, bool] = {}
        self.failed_user_challenges: dict[str, int] = {}
        self.TERMINAL_ANSWERS = "0 no none stop quit exit break cancel abort".split()
        self.AFFIRMATIVE_ANSWERS = (
            "yes yeah y yup affirmative ye sure yeh please".split()
        )
        self.NEGATIVE_ANSWERS = "no nope n negatory nuh-uh nah".split()
        self.FIRST_DEVICE_PLEASE = "Please answer from your phone or primary device!"
        super().__init__(bot_number)

    async def handle_message(self, message: Message) -> Response:

        # import pdb;pdb.set_trace()
        pending_answer, probably_future = get_source_or_uuid_from_dict(
            message, self.pending_answers
        )
        _, requires_first_device = get_source_or_uuid_from_dict(
            message, self.requires_first_device
        )

        if message.full_text and pending_answer:
            if requires_first_device and not is_first_device(message):
                return self.FIRST_DEVICE_PLEASE
            self.requires_first_device.pop(message.source, None)
            self.requires_first_device.pop(message.uuid, None)
            if probably_future:
                probably_future.set_result(message)
            return None
        return await super().handle_message(message)

    async def ask_freeform_question(
        self,
        recipient: Union[str, tuple[str, str]],
        question_text: Optional[str] = "What's your favourite colour?",
        require_first_device: bool = False,
    ) -> str:
        """UrQuestion that all other questions use. Asks a question fulfilled by a sentence or short answer."""
        group = ""
        if isinstance(recipient, tuple):
            recipient, group = recipient
        answer_future = self.pending_answers[recipient, group] = asyncio.Future()
        if require_first_device:
            self.requires_first_device[recipient] = True

        if question_text:
            if group:
                await self.send_message(None, question_text, group=group)
            else:
                await self.send_message(recipient, question_text)
        answer = await answer_future
        self.pending_answers.pop((recipient, group))
        return answer.full_text or ""

    async def ask_floatable_question(
        self,
        recipient: str,
        question_text: Optional[str] = "What's the price of gasoline where you live?",
        require_first_device: bool = False,
    ) -> Optional[float]:
        """Asks a question answered with a floating point or decimal number.
        Asks user clarifying questions if an invalid number is provided.
        Returns None if user says any of the terminal answers."""

        answer = await self.ask_freeform_question(
            recipient, question_text, require_first_device
        )
        answer_text = answer

        # This checks to see if the answer is a valid candidate for float by replacing
        # the first comma or decimal point with a number to see if the resulting string .isnumeric()
        if answer_text and not (
            answer_text.replace(".", "1", 1).isnumeric()
            or answer_text.replace(",", "1", 1).isnumeric()
        ):
            # cancel if user replies with any of the terminal answers "stop, cancel, quit, etc. defined above"
            if answer.lower() in self.TERMINAL_ANSWERS:
                return None

            # Check to see if the original question already specified wanting the answer as a decimal.
            # If not asks the question again and adds "as a decimal" to clarify
            if question_text and "as a decimal" in question_text:
                return await self.ask_floatable_question(recipient, question_text)
            return await self.ask_floatable_question(
                recipient, (question_text or "") + " (as a decimal, ie 1.01 or 2,02)"
            )
        if answer_text:
            return float(answer.replace(",", ".", 1))
        return None

    async def ask_intable_question(
        self,
        recipient: str,
        question_text: Optional[str] = "How many years old do you wish you were?",
        require_first_device: bool = False,
    ) -> Optional[int]:
        """Asks a question answered with an integer or whole number.
        Asks user clarifying questions if an invalid number is provided.
        Returns None if user says any of the terminal answers."""

        answer = await self.ask_freeform_question(
            recipient, question_text, require_first_device
        )
        if answer and not answer.isnumeric():

            # cancel if user replies with any of the terminal answers "stop, cancel, quit, etc. defined above"
            if answer.lower() in self.TERMINAL_ANSWERS:
                return None

            # Check to see if the original question already specified wanting the answer as a decimal.
            # If not asks the question again and adds "as a whole number, ie '1' or '2000'" to clarify
            if question_text and "as a whole number" in question_text:
                return await self.ask_intable_question(recipient, question_text)
            return await self.ask_intable_question(
                recipient,
                (question_text or "") + " (as a whole number, ie '1' or '2000')",
            )
        if answer:
            return int(answer)
        return None

    async def ask_yesno_question(
        self,
        recipient: str,
        question_text: str = "Are you sure? yes/no",
        require_first_device: bool = False,
    ) -> Optional[bool]:
        """Asks a question that expects a yes or no answer. Returns a Boolean:
        True if Yes False if No. None if cancelled"""

        # ask the question as a freeform question
        answer = await self.ask_freeform_question(
            recipient, question_text, require_first_device
        )
        answer = answer.lower().rstrip(string.punctuation)
        # if there is an answer and it is negative or positive
        if answer and answer in (self.AFFIRMATIVE_ANSWERS + self.NEGATIVE_ANSWERS):
            # return true if it's in affirmative answers otherwise assume it was negative and return false
            if answer in self.AFFIRMATIVE_ANSWERS:
                return True
            return False

        # return none if user answers cancel, etc
        if answer and answer in self.TERMINAL_ANSWERS:
            return None

        # if the answer is not a terminal answer but also not a match, add clarifier and ask again
        if "Please answer yes or no" not in question_text:
            question_text = "Please answer yes or no, or cancel:\n \n" + question_text

        return await self.ask_yesno_question(
            recipient, question_text, require_first_device
        )

    async def ask_address_question_(
        self,
        recipient: str,
        question_text: str = "What's your shipping address?",
        require_confirmation: bool = False,
    ) -> Optional[dict]:
        """Asks user for their address and verifies through the google maps api
        Can ask User for confirmation, returns string with formatted address or none"""
        # get google maps api key from secrets
        api = utils.get_secret("GOOGLE_MAPS_API")
        if not api:
            logging.error("Error, missing Google Maps API in secrets configuration")
            return None
        # ask for the address as a freeform question
        address = await self.ask_freeform_question(recipient, question_text)
        # we take the answer provided by the user, format it nicely as a request to google maps' api
        # It returns a JSON object from which we can ascertain if the address is valid
        async with self.client_session.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": api},
        ) as resp:
            address_json = await resp.json()
        # if google can't find the address results will be empty
        if not (address_json["results"]):
            # break out if user replied cancel, exit, stop, etc.
            if address.lower() in self.TERMINAL_ANSWERS:
                return None
            # Otherwise, apologize and ask again
            await self.send_message(
                recipient,
                "Sorry, I couldn't find that. \nPlease try again or reply cancel to cancel \n",
            )
            return await self.ask_address_question_(
                recipient, question_text, require_confirmation
            )
        # if maps does return a formatted address
        if address_json["results"] and address_json["results"][0]["formatted_address"]:
            if require_confirmation:
                # Tell user the address we got and ask them to confirm
                # Give them a google Maps link so they can check
                maybe_address = address_json["results"][0]["formatted_address"]
                maps_url = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote_plus(maybe_address)}&query_place_id={address_json['results'][0]['place_id']}"
                confirmation = await self.ask_yesno_question(
                    recipient,
                    f"Got: \n{maybe_address} \n\n{maps_url} \n\nIs this your address? (yes/no)",
                )
                # If not, ask again
                if not confirmation:
                    return await self.ask_address_question_(
                        recipient,
                        question_text,
                        require_confirmation,
                    )
            return address_json["results"][0]  # ["formatted_address"]
        # If we made it here something unexpected probably went wrong.
        # Google returned something but didn't have a formatted address
        return None

    async def ask_address_question(
        self,
        recipient: str,
        question_text: str = "What's your shipping address?",
        require_confirmation: bool = False,
    ) -> Optional[dict]:
        addr = await self.ask_address_question_(
            recipient, question_text, require_confirmation
        )
        if addr:
            return addr["formatted_address"]
        return addr

    async def ask_multiple_choice_question(  # pylint: disable=too-many-arguments
        self,
        recipient: str,
        question_text: Optional[str],
        options: Union[dict[str, str], list[str]],
        require_confirmation: bool = True,
        require_first_device: bool = False,
    ) -> Optional[str]:
        """Prompts the user to select from a series of options.
        Behaviour alters slightly based on options:
        options as list -> we write labels for you with "1,2,3,...."
        options as dict -> dict keys are the labels
        options as dict with all values "" -> the labels are the options,
        and only labels are printed"""
        ## TODO: allow fuzzy answers or lowercase answers. Needs design discussion.

        # Check to ensure that user is on their first device as opposed to a linked device
        # Important for certain questions involving payment addresses
        if require_first_device:
            self.requires_first_device[recipient] = True

        if question_text is None:
            question_text = "Pick one from these options:"

        options_text = ""

        # User can pass just a list of options and we generate labels for them using enumerate
        # User can provide their own labels for the options by passing a dict
        # Create a question with just labels by having all values be ""
        # This will format the options text and check for a just labels question
        if isinstance(options, list):
            dict_options: dict[Any, str] = {
                str(i): value for i, value in enumerate(options, start=1)
            }
        else:
            dict_options = options

        # Put ) between labels and text, if dict is all empty values leave blank
        spacer = ") " if any(dict_options.values()) else ""

        # We use a generator object to join all the options
        # into one text that can be sent to the user
        options_text = " \n".join(
            f"{label}{spacer}{body}" for label, body in dict_options.items()
        )

        # for the purposes of making it case insensitive, make sure no options are the same when lowercased
        lower_dict_options = {k.lower(): v for (k, v) in dict_options.items()}
        if len(lower_dict_options) != len(dict_options):
            raise ValueError("Need to ensure unique options when lower-cased!")

        # send user the formatted question as a freeform question and process their response
        answer = await self.ask_freeform_question(
            recipient, question_text + "\n" + options_text, require_first_device
        )

        # when there is a match
        if answer and answer.lower() in lower_dict_options.keys():

            # if confirmation is required ask for it as a yes/no question
            if require_confirmation:
                confirmation_text = (
                    "You picked: \n"
                    + answer
                    + spacer
                    + lower_dict_options[answer.lower()]
                    + "\n\nIs this correct? (yes/no)"
                )
                confirmation = await self.ask_yesno_question(
                    recipient, confirmation_text
                )

                # if no, ask the question again
                if not confirmation:
                    return await self.ask_multiple_choice_question(
                        recipient,
                        question_text,
                        dict_options,
                        require_confirmation,
                        require_first_device,
                    )
        # if the answer given does not match a label
        if answer and not answer.lower() in lower_dict_options.keys():
            # return none and exit if user types cancel, stop, exit, etc...
            if answer.lower() in self.TERMINAL_ANSWERS:
                return None
            # otherwise reminder to type the label exactly as it appears and restate the question
            if "Please reply" not in question_text:
                question_text = (
                    "Please reply with just the label exactly as typed \n \n"
                    + question_text
                )
            return await self.ask_multiple_choice_question(
                recipient,
                question_text,
                dict_options,
                require_confirmation,
                require_first_device,
            )
        # finally return the option that matches the answer, or if empty the answer itself
        return lower_dict_options[answer.lower()] or answer

    async def ask_email_question(
        self,
        recipient: str,
        question_text: str = "Please enter your email address",
    ) -> Optional[str]:
        """Prompts the user to enter an email address, and validates with a very long regular expression"""

        # ----SETUP----
        # ask for the email address as a freeform question instead of doing it ourselves
        answer = await self.ask_freeform_question(recipient, question_text)

        # ----VALIDATE----
        # if answer contains a valid email address, add it to maybe_email
        maybe_match = re.search(
            r"""(?:[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*|"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])*")@(?:(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?|\[(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?|[a-z0-9-]*[a-z0-9]:(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21-\x5a\x53-\x7f]|\\[\x01-\x09\x0b\x0c\x0e-\x7f])+)\])""",
            answer,
        )
        # maybe_email is a re.match object, which returns only if there is a match.
        if maybe_match:
            email = maybe_match.group(0)

        # ----INVALID?----
        # If we have an answer, but no matched email
        if answer and not maybe_match:
            # return none and exit if user types cancel, stop, exit, etc...
            if answer.lower() in self.TERMINAL_ANSWERS:
                return None

            # ----INVALID REPROMPT----
            # if the answer is not a valid email address, ask the question again, but don't let it add "Please reply" forever
            if "Please reply" not in question_text:
                question_text = (
                    "Please reply with a valid email address\n\n" + question_text
                )

            return await self.ask_email_question(recipient, question_text)

        return email

    @hide
    async def do_challenge(self, msg: Message) -> Response:
        """Challenges a user to do a simple math problem,
        optionally provided as an image to increase attacker complexity."""
        # the captcha module delivers graphical challenges of the same format
        if captcha is not None:
            challenge, answer = captcha.get_challenge_and_answer()
            await self.send_message(
                msg.uuid,
                "Please answer this arithmetic problem to prove you're (probably) not a bot!",
                attachments=[challenge],
            )
        else:
            offset = secrets.randbelow(20)
            challenge = f"What's the sum of one and {offset}?"
            answer = offset + 1
            await self.send_message(msg.uuid, challenge)
        # we already asked the question, either with an attachment, or using the reduced-scope challenge
        # so question here is None (waits for answer)
        maybe_answer = await self.ask_intable_question(msg.uuid, None)
        if maybe_answer != answer:
            # handles empty case, but has no logic as to what to do if the user exceeds a threshold
            self.failed_user_challenges[msg.uuid] = (
                self.failed_user_challenges.get(msg.uuid, 0) + 1
            )
            return await self.do_challenge(msg)
        return "Thanks for helping protect our community!"

    @requires_admin
    async def do_setup(self, msg: Message) -> str:
        if not utils.AUXIN:
            return "Can't set profile without auxin"
        fields: dict[str, Optional[str]] = {}
        for field in ["given_name", "family_name", "about", "mood_emoji"]:
            resp = await self.ask_freeform_question(
                msg.source, f"value for field {field}?"
            )
            if resp and resp.lower() == "skip":
                break
            if resp and resp.lower() != "none":
                fields[field] = resp
        fields["payment_address"] = mc_util.b58_wrapper_to_b64_public_address(
            await self.mobster.ensure_address()
        )
        attachments = await get_attachment_paths(msg)
        if attachments:
            fields["profile_path"] = attachments[0]
        await self.set_profile_auxin(**fields)
        return f"set {', '.join(fields)}"


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
    resp = await bot.wait_for_response(rpc_id=rpc_id)
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


def fmt_ms(ts: int) -> str:
    return datetime.datetime.utcfromtimestamp(ts / 1000).isoformat()


async def metrics(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    return web.Response(
        status=200,
        text="start_time, command, delta\n"
        + "\n".join(
            f"{fmt_ms(t)}, {cmd}, {delta}"
            for t, cmd, delta in bot.signal_roundtrip_latency
        ),
    )


async def restart(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    bot.restart_task = asyncio.create_task(bot.start_process())
    bot.restart_task.add_done_callback(bot.log_task_result)
    return web.Response(status=200)


app = web.Application()


async def recipients(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    recipeints = open(f"data/{bot.bot_number}.d/recipients-store").read()
    return web.Response(status=200, text=recipeints)


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
        web.post("/restart", restart),
        web.get("/metrics", aio.web.server_stats),
        web.get("/csv_metrics", metrics),
        web.get("/recipients", recipients),
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
