#!/usr/bin/python3.9
"""
The core chatbot framework: Message, Signal, Bot, and app
"""
import asyncio
import asyncio.subprocess as subprocess  # https://github.com/PyCQA/pylint/issues/1469
import datetime
import json
import logging
import os
import signal
import sys
import time
import traceback
import uuid
import urllib
from asyncio import Queue, StreamReader, StreamWriter
from asyncio.subprocess import PIPE
from typing import Any, AsyncIterator, Optional, Union

import aiohttp
import phonenumbers as pn
import termcolor
from aiohttp import web
from phonenumbers import NumberParseException
from prometheus_async import aio
from prometheus_client import Summary, Histogram

# framework
import mc_util
from forest import autosave, datastore, payments_monitor, pghelp, utils
from forest.message import AuxinMessage, Message

JSON = dict[str, Any]
Response = Union[str, list, dict[str, str], None]

roundtrip_histogram = Histogram("roundtrip_h", "Roundtrip message response time")
roundtrip_summary = Summary("roundtrip_s", "Roundtrip message response time")


def rpc(method: str, _id: str = "1", **params: Any) -> dict:
    return {"jsonrpc": "2.0", "method": method, "id": _id, "params": params}


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
            command = f"{utils.ROOT_DIR}/auxin-cli --config {utils.ROOT_DIR} --user {self.bot_number} jsonRpc".split()
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

    async def handle_auxincli_raw_line(self, line: str) -> None:
        if '{"jsonrpc":"2.0","result":[],"id":"receive"}' not in line:
            pass # logging.debug("auxin: %s", line)
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
        try:
            if "params" in blob:
                if isinstance(blob["params"], list):
                    for msg in blob["params"]:
                        if not blob.get("content", {}).get("receipt_message", {}):
                            await self.auxincli_output_queue.put(AuxinMessage(msg))
                    return
                await self.auxincli_output_queue.put(AuxinMessage(blob["params"]))
            if "result" in blob:
                if isinstance(blob.get("result"), list):
                    # idt this happens anymore, remove?
                    logging.info("results list code path")
                    for msg in blob.get("result"):
                        if not blob.get("content", {}).get("receipt_message", {}):
                            await self.auxincli_output_queue.put(AuxinMessage(msg))
                    return
                msg = AuxinMessage(blob)
                await self.auxincli_output_queue.put(msg)
        except KeyError:
            logging.info("auxin parse error: %s", line)
            traceback.print_exception(*sys.exc_info())
            return
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

    async def wait_resp(self, cmd: dict) -> Message:
        future_key = cmd["method"] + "-" + str(round(time.time()))
        logging.info("expecting response id: %s", future_key)
        cmd["id"] = future_key
        self.pending_requests[future_key] = asyncio.Future()
        await self.auxincli_input_queue.put(cmd)
        result = await self.pending_requests[future_key]
        self.pending_requests.pop(future_key)
        return result

    async def auxin_req(self, method: str, **params: Any) -> Message:
        return await self.wait_resp(rpc(method, **params))

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
        logging.info(profile)

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
            return [await self.send_message(recipient, m) for m in msg][-1]
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
            params["group"] = group
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
        params["destination"] = str(recipient)
        # maybe use rpc() instead
        future_key = f"send-{int(time.time()*1000)}-{hex(hash(msg))[-4:]}"
        json_command: JSON = {
            "jsonrpc": "2.0",
            "id": future_key,
            "method": "send",
            "params": params,
        }
        self.pending_requests[future_key] = asyncio.Future()
        await self.auxincli_input_queue.put(json_command)
        return future_key

    async def admin(self, msg: Response) -> None:
        await self.send_message(utils.get_secret("ADMIN"), msg)

    async def respond(self, target_msg: Message, msg: Response) -> str:
        """Respond to a message depending on whether it's a DM or group"""
        if not target_msg.source:
            logging.error(target_msg.blob)
        if not utils.AUXIN and isinstance(
            target_msg.group, str
        ):  # and it's a valid b64
            return await self.send_message(None, msg, group=target_msg.group)
        destination = target_msg.source or target_msg.uuid
        return await self.send_message(destination, msg)

    # FIXME: disable for auxin
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
        await self.auxincli_input_queue.put(react)

    async def auxincli_input_iter(self) -> AsyncIterator[dict]:
        """Provides an asynchronous iterator over pending auxin-cli commands"""
        while True:
            command = await self.auxincli_input_queue.get()
            yield command

    # maybe merge with the above?
    async def write_commands(self, pipe: StreamWriter) -> None:
        """Encode and write pending auxin-cli commands"""
        async for msg in self.auxincli_input_iter():
            if not msg.get("method"):
                print(msg)
            if msg.get("method") != "receive":
                logging.info("input to signal: %s", json.dumps(msg))
            if pipe.is_closing():
                logging.error("auxin-cli stdin pipe is closed")
            pipe.write(json.dumps(msg).encode() + b"\n")
            await pipe.drain()


Datapoint = tuple[int, str, float]  # timestamp in ms, command/info, latency in seconds


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
        self.pending_response_tasks: list[asyncio.Task] = []
        self.restart_task = asyncio.create_task(
            self.start_process()
        )  # maybe cancel on sigint?
        self.queue_task = asyncio.create_task(self.handle_messages())
        self.response_metrics: list[Datapoint] = []
        self.auxin_roundtrip_latency: list[Datapoint] = []
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
        note = message.command or ""
        if python_delta:
            self.response_metrics.append((int(start_time), note, python_delta))
        if future_key:
            logging.debug("awaiting future %s", future_key)
            result = await self.pending_requests[future_key]
            self.pending_requests.pop(future_key)
            roundtrip_delta = (result.timestamp - message.timestamp) / 1000
            self.auxin_roundtrip_latency.append(
                (message.timestamp, note, roundtrip_delta)
            )
            roundtrip_summary.observe(roundtrip_delta)
            roundtrip_histogram.observe(roundtrip_delta)
            logging.info("noted roundtrip time: %s", roundtrip_delta)
            await self.admin(
                f"command: {note}. python delta: {python_delta}s. roundtrip delta: {roundtrip_delta}s",
            )

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

    # gross
    async def do_average_metric(self, _: Message) -> Response:
        avg = sum(metric[-1] for metric in self.auxin_roundtrip_latency) / len(
            self.auxin_roundtrip_latency
        )
        return str(round(avg, 4))

    async def do_dump_metric_csv(self, _: Message) -> Response:
        return "start_time, command, delta\n" + "\n".join(
            f"{fmt_ms(t)}, {cmd}, {delta}" for t, cmd, delta in self.response_metrics
        )

    async def do_dump_roundtrip(self, _: Message) -> Response:
        return "start_time, command, delta\n" + "\n".join(
            f"{fmt_ms(t)}, {cmd}, {delta}"
            for t, cmd, delta in self.auxin_roundtrip_latency
        )

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

    async def do_exception(self, message: Message) -> None:
        raise Exception("You asked for it!")

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


async def admin_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    msg = urllib.parse.unquote(request.query.get("message", ""))
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

if utils.MEMFS:
    app.on_startup.append(autosave.start_memfs)
    app.on_startup.append(autosave.start_memfs_monitor)

app.add_routes([])

if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = Bot()

    web.run_app(app, port=8080, host="0.0.0.0")
