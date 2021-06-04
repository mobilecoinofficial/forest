import typing as T
# we're using py3.9, only Optional is needed
from typing import Optional, List, Dict 
import asyncio
import json
import os
import subprocess
import urllib.parse
import random

from aiohttp import web
import aiohttp
import aioprocessing

from forest_tables import RoutingManager, PaymentsManager, UserManager

HOSTNAME = open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID


class Message:
    def __init__(self, blob: dict) -> None:
        self.envelope = envelope = blob.get("envelope", {})
        # {'envelope': {'source': '+15133278483', 'sourceDevice': 2, 'timestamp': 1621402445257, 'receiptMessage': {'when': 1621402445257, 'isDelivery': True, 'isRead': False, 'timestamps': [1621402444517]}}}
        self.error = envelope.get("error")
        self.receipt = envelope.get("receiptMessage")
        self.source: str = envelope.get("source")
        self.ts = round(envelope.get("timestamp", 0) / 1000)
        msg = envelope.get("dataMessage", {})
        self.full_text = self.text = msg.get("message", "")
        self.reactions: Dict[str, str] = {}
        self.command: Optional[str] = None
        self.tokens: Optional[List[str]] = None
        # if self.source in wisp.user_callbacks:
        #    self.tokens = self.text.split(" ")
        if self.text and self.text.startswith("/"):
            command, *self.tokens = self.text.split(" ")
            self.command = command[1:]  # remove /
            self.arg1 = self.tokens[0] if self.tokens else None
            self.text = (
                " ".join(self.tokens[1:]) if len(self.tokens) > 1 else None
            )

    def __repr__(self) -> str:
        return f"<{self.envelope}>"


class Session:
    def __init__(
        self,
        user,
        dialout_address,
        user_manager=None,
        routing_manager=None,
        payments_manager=None,
    ):
        self.user = user
        self.dialout_address = dialout_address
        self.loop = asyncio.get_event_loop()
        self.proc = None
        self.dialout_ws = None
        self.filepath = "/app/data/+" + user
        self.message_queue = asyncio.Queue()
        self.client_session = aiohttp.ClientSession()
        self.scratch = {"payments": {}}
        if user_manager:
            self.user_manager = user_manager
        else:
            self.user_manager = UserManager()
        if payments_manager:
            self.payments_manager = payments_manager
        else:
            self.payments_manager = PaymentsManager()

    async def get_file(self):
        from_pgh = await self.user_manager.get_user(self.user)
        open(self.filepath, "w").write(from_pgh[0].get("account"))
        update_claim = await self.user_manager.mark_user_claimed(
            self.user, HOSTNAME
        )
        return update_claim

    async def mark_freed(self):
        return await self.user_manager.mark_user_freed(self.user)

    async def put_file(self):
        file_contents = open(self.filepath, "r").read()
        return await self.user_manager.set_user(self.user, file_contents)

    async def send_sms(self, source, destination, message_text) -> dict:
        payload = {
            "source": source,
            "destination": destination,
            "message": message_text,
        }
        open("/dev/stdout", "w").write(f"{payload}\n")
        response = await self.client_session.post(
            "https://api.teleapi.net/sms/send?token="
            + os.environ.get("TELI_KEY"),
            data=payload,
        )
        response_json = await response.json()
        return response_json

    async def send_message(self, recipient, msg):
        if isinstance(msg, list):
            return [await self.send_message(recipient, m) for m in msg]
        if isinstance(msg, dict):
            msg = "\n".join((f"{key}:\t{value}" for key, value in msg.items()))
        json_command = json.dumps(
            {
                "command": "send",
                "recipient": [str(recipient)],
                "message": msg,
            }
        )
        self.proc.stdin.write(json_command.encode() + b"\n")

    async def message_iter(self):
        """Provides an asynchronous iterator over messages on the queue."""
        while True:
            message = await self.message_queue.get()
            yield message

    async def register(self, message):
        new_user = message.source
        usdt_price = 15.00
        # invpico = 100000000000 # doesn't work in mixin
        invnano = 100000000
        try:
            last_val = await self.client_session.get(
                "https://big.one/api/xn/v1/asset_pairs/8e900cb1-6331-4fe7-853c-d678ba136b2f"
            )
            resp_json = await last_val.json()
            mob_rate = float(resp_json.get("data")[0].get("close"))
        except:
            # big.one goes down sometimes, if it does... make up a price
            mob_rate = 14
        # perturb each price slightly
        mob_rate -= random.random() / 1000
        mob_price = usdt_price / mob_rate
        nmob_price = int(mob_price * invnano)
        mob_price_exact = nmob_price / invnano
        continue_message = f"The current price for a SMS number is {mob_price_exact}MOB/month. If you would like to continue, please send exactly..."
        await self.send_message(
            new_user,
            [
                continue_message,
                f"{mob_price_exact}",
                "to",
                "nXz8gbcAfHQQUwTHuQnyKdALe5oXKppDn9oBRms93MCxXkiwMPnsVRp19Vrmb1GX6HdQv7ms83StXhwXDuJzN9N7h3mzFnKsL6w8nYJP4q",
                "Upon payment, you will be able to select the area code for your new phone number!",
            ],
        )
        # check for payments every 10s for 1hr
        for _ in range(360):
            payment_done = await self.payments_manager.get_payment(
                nmob_price * 1000
            )
            if payment_done:
                payment_done = payment_done[0]
                await self.send_message(
                    new_user,
                    [
                        "Thank you for your payment! Please save this transaction ID for your records and include it with any customer service requests. Without this payment ID, it will be harder to verify your purchase.",
                        f"{payment_done.get('transaction_log_id')}",
                        'Please finish setting up your account at your convenience with the "/status" command.',
                    ],
                )
                self.scratch["payments"][new_user] = payment_done.get(
                    "transaction_log_id"
                )
                return True
            await asyncio.sleep(10)

    async def handle_messages(self):
        async for message in self.message_iter():
            open("/dev/stdout", "w").write(f"{message}\n")
            if message.source:
                maybe_routable = await RoutingManager().get_id(
                    message.source.strip("+")
                )
            else:
                maybe_routable = None
            if maybe_routable:
                numbers = [registered.get("id") for registered in maybe_routable]
            else:
                numbers = None
            if numbers and message.command == "send":
                response = await self.send_sms(
                    source=numbers[0],
                    destination=message.arg1,
                    message_text=message.text,
                )
                sms_uuid = response.get("data")
                # TODO: store message.source and sms_uuid in a queue, enable https://apidocs.teleapi.net/api/sms/delivery-notifications
                #    such that delivery notifs get redirected as responses to send command
                await self.send_message(message.source, response)
            elif message.command == "help":
                await self.send_message(
                    message.source,
                    """Welcome to the Forest.contact Pre-Release!\nTo get started, try /register, or /status! If you've already registered, try to send a message via /send.""",
                )
            elif message.command == "register":
                asyncio.create_task(self.register(message))
            elif message.command == "status":
                # paid but not registered
                if self.scratch["payments"].get(message.source) and not numbers:
                    await self.send_message(
                        message.source,
                        [
                            "Welcome to the beta! Thank you for your payment. Please contact support to finish setting up your account by requesting to join this group. We will reach out within 12 hours.",
                            "https://signal.group/#CjQKINbHvfKoeUx_pPjipkXVspTj5HiTiUjoNQeNgmGvCmDnEhCTYgZZ0puiT-hUG0hUUwlS",
                        ],
                    )
                # registered, one number
                elif numbers and len(numbers) == 1:
                    await self.send_message(
                        message.source,
                        f'Hi {message.source}! We found {numbers[0]} registered for your user. Try "/send {message.source} Hello from Forest Contact via {numbers[0]}!".',
                    )
                # registered, many numbers
                elif numbers:
                    await self.send_message(
                        message.source,
                        f"Hi {message.source}! We found several numbers {numbers} registered for your user. Try '/send {message.source} Hello from Forest Contact via {numbers[0]}!'.",
                    )
                # not paid, not registered
                else:
                    await self.send_message(
                        message.source,
                        f'We don\'t see any Forest Contact numbers for your account! If you would like to register a new number, try "/register" and following the instructions.',
                    )
            elif message.command or message.text:
                await self.send_message(
                    message.source,
                    f"Sorry! Command {message.command} not recognized! Try /help. \n{message}",
                )

    async def launch_and_connect(self):
        await self.get_file()
        for _ in range(5):
            if os.path.exists(self.filepath):
                break
            await asyncio.sleep(1)
        COMMAND = f"/app/signal-cli --config /app --username=+{self.user} --output=json stdio".split()

        self.proc = await asyncio.subprocess.create_subprocess_exec(
            *COMMAND,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        print(f"started signal-cli @ {self.user} with PID {self.proc.pid}")

        # public String commandName;
        # public String recipient;
        # public String content;
        # public JsonNode details;

        async with aiohttp.ClientSession() as session:
            self.dialout_ws = await session.ws_connect(
                "http://127.0.0.1:8079/ws"
            )
            asycnio.create_task(
                spool_lines_to_cb(self.proc.stdout, self.dialout_ws.send_str)
            )
            await self.dialout_ws.send_str(
                # this is weird, idk what this is supposed to do
                json.dumps(
                    {
                        "commandName": "getVersion",
                        "recipient": None,
                        "content": "v0.1.0",
                        "details": {
                            "client": "oasismsg",
                            "region": os.environ.get("FLY_REGION", "None"),
                        },
                    }
                )
            )
            async for msg in self.dialout_ws:
                # still figuring out ws message types
                if msg.type == aiohttp.WSMsgType.TEXT:
                    msg_contents = msg.data
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    try:
                        msg_contents = msg.data.decode()
                    except:
                        msg_contents = ""
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
                msg_loaded = json.loads(msg_contents)
                open("/dev/stdout", "w").write(f"{msg_loaded}\n")

                await self.message_queue.put(Message(msg_loaded))
                if msg_loaded.get("command") in ("send", "updateGroup"):
                    self.proc.stdin.write(msg_loaded.encode() + b"\n")
            print("done with ws")
        await self.proc.wait()


async def spool_lines_to_cb(
    stream: asyncio.StreamReader, callback: T.Callable[[bytes], None]
):

    while True:
        line = await stream.readline()
        if not line:
            break
        await callback(line.decode())


async def noGet(request):
    raise web.HTTPFound(location="https://signal.org/")


async def get_handler(request):
    account = request.match_info.get("phonenumber")
    session = request.app.get("session")
    status = ""
    if not session:
        request.app["session"] = new_session = Session(
            account,
            "",
        )
        asyncio.create_task(new_session.launch_and_connect())
        asyncio.create_task(new_session.handle_messages())
        status = "launched"
    else:
        status = str(session)
    return web.json_response({"status": status})


async def send_message_handler(request):
    account = request.match_info.get("phonenumber")
    session = request.app.get("session")
    msg_data = await request.text()
    msg_obj = {x: y[0] for x, y in urllib.parse.parse_qs(msg_data).items()}
    recipient = msg_obj.get("recipient", "+15133278483")
    if session:
        await session.send_message(recipient, msg_data)
    return web.json_response({"status": "sent"})


async def inbound_handler(request):
    msg_data = await request.text()
    # parse query-string encoded sms/mms into object
    msg_obj = {x: y[0] for x, y in urllib.parse.parse_qs(msg_data).items()}
    # if it's a raw post (debugging / oops / whatnot - not a query string)
    if not msg_obj:
        # stick the contents under the message key
        msg_obj["message"] = msg_data
    destination = msg_obj.get("destination")
    ## lookup sms recipient to signal recipient
    recipient = {}.get(destination, "+15133278483")
    maybe_dest = await request.app["routing_manager_connection"].get_destination(
        destination
    )
    if maybe_dest:
        recipient = maybe_dest[0].get("destination")
    msg_obj["maybe_dest"] = str(maybe_dest)
    session = request.app.get("session")
    if session:
        # send hashmap as signal message with newlines and tabs and stuff
        await session.send_message(recipient, msg_obj)
        return web.Response(text="TY!")

    return web.Response(status=504, text="Sorry, no live workers.")
    # TODO: return non-200 if no delivery receipt / ok crypto state, let teli do our retry
    # no live worker sessions
    return await request.app["client_session"].post(
        "https://counter.pythia.workers.dev/post", data=msg_data
    )


# ["->", "fsync", "/+14703226669", "(1, 2)", "/app/signal-cli", ["/app/signal-cli", "--config", "/app", "--username=+14703226669", "--output=json", "stdio", ""], 0, 0, 523]
# ["<-", "fsync", "0"]
async def start_queue_monitor(app):
    async def background_sync_handler():
        queue = app["mem_queue"]
        while True:
            queue_item = await queue.coro_get()
            # iff fsync triggered by signal-cli
            if (
                queue_item[0:2] == ["->", "fsync"]
                and queue_item[5][0] == "/app/signal-cli"
            ):
                # /+14703226669
                file_to_sync = queue_item[2]
                # 14703226669
                maybe_session = app.get("session")
                if maybe_session:
                    await maybe_session.put_file()

    app["mem_task"] = asyncio.create_task(background_sync_handler())


async def on_shutdown(app):
    session = app.get("session")
    if session:
        session.proc.kill()
        await session.proc.wait()
        await session.put_file()
        await session.mark_freed()
        await session.dialout_ws.close(
            code=aiohttp.WSCloseCode.GOING_AWAY, message="Server shutdown"
        )


async def start_websocat(app):
    cmd = "/app/websocat --text -E ws-listen:127.0.0.1:8079 broadcast:mirror: --restrict-uri=/ws".split()
    app["websocat_proxy"] = asyncio.create_task(
        asyncio.subprocess.create_subprocess_exec(*cmd)
    )


async def start_memfs(app):

    import fuse
    import mem

    app["mem_queue"] = aioprocessing.AioQueue()
    mem_queue = app["mem_queue"]
    if not os.path.exists("/dev/fuse"):
        proc = subprocess.Popen(
            ["/usr/sbin/insmod", "/app/fuse.ko"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait()
        (stdout, stderr) = proc.communicate()

    def memfs_proc(path="data"):
        pid = os.getpid()
        open("/dev/stdout", "w").write(
            f"Starting memfs with PID: {pid} on dir: {path}\n"
        )
        return fuse.FUSE(mem.Memory(logqueue=mem_queue), "data")

    async def launch():
        memfs = aioprocessing.AioProcess(target=memfs_proc)
        memfs.start()
        app["memfs"] = memfs

    await launch()


async def start_sessions(app):
    app["user_manager_connection"] = UserManager()
    app["routing_manager_connection"] = RoutingManager()
    app["payments_manager_connection"] = PaymentsManager()


app = web.Application()

app.on_shutdown.append(on_shutdown)
app.on_startup.append(start_memfs)
app.on_startup.append(start_websocat)
app.on_startup.append(start_queue_monitor)
app.on_startup.append(start_sessions)

app.add_routes(
    [
        web.get("/", noGet),
        web.post("/user/{phonenumber}", send_message_handler),
        web.get("/user/{phonenumber}", get_handler),
        web.post("/inbound", inbound_handler),
    ]
)

app["session"] = None


if __name__ == "__main__":
    web.run_app(app, port=8080, host="0.0.0.0")
