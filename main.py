import asyncio
import ctypes
import json
import os
import queue
import sys
import threading
import time
import typing as T

import aiohttp
import aioprocessing
from aiohttp import web

import logging
import subprocess
import time
import urllib.parse

import pghelp

USER_DATABASE = os.getenv("USER_DATABASE")

UserPGExpressions = pghelp.PGExpressions(
    table="users",
    get_user="SELECT account FROM {self.table} WHERE id=$1;",  # AND
    mark_user_claimed="UPDATE {self.table} \
        SET last_claimed_ms = (extract(epoch from now()) * 1000) \
        WHERE id=$1;",
    mark_user_freed="UPDATE {self.table} SET last_claimed_ms = 0 WHERE id=$1;",
    mark_user_update="UPDATE {self.table} SET \
        last_update_ms = (extract(epoch from now()) * 1000) \
        WHERE id=$1;",
    put_user="UPDATE {self.table} SET \
            account = $2, \
            last_update_ms = (extract(epoch from now()) * 1000) \
            WHERE id=$1;",
)


class UserManager(pghelp.PGInterface):
    """Abstraction for operations on the `user` table."""

    def __init__(
        self, queries=UserPGExpressions, database=USER_DATABASE, loop=None
    ) -> None:
        super().__init__(queries, database, loop)


class Session:
    def __init__(self, user, dialout_address, pghelper=None):
        self.user = user
        self.dialout_address = dialout_address
        self.loop = asyncio.get_event_loop()
        self.proc = None
        self.dialout_ws = None
        self.filepath = "/app/data/+" + user
        if pghelper:
            self.pghelper = pghelper
        else:
            self.pghelper = UserManager()

    async def get_file(self):
        from_pgh = await self.pghelper.get_user(self.user)
        open(self.filepath, "w").write(from_pgh[0].get("account"))
        update_claim = await self.pghelper.mark_user_claimed(self.user)
        return update_claim

    async def mark_freed(self):
        return await self.pghelper.mark_user_freed(self.user)

    async def put_file(self):
        file_contents = open(self.filepath, "r").read()
        return await self.pghelper.put_user(self.user, file_contents)

    async def send_message(self, recipient, msg):
        json_command = json.dumps(
            {"commandName": "sendMessage", "recipient": str(recipient), "content": msg}
        )
        self.proc.stdin.write(json_command.encode() + b"\n")

    async def launch_and_connect(self):
        await self.get_file()
        for _ in range(5):
            if os.path.exists(self.filepath):
                break
            else:
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
            self.dialout_ws = await session.ws_connect("http://127.0.0.1:8079/ws")
            self.loop.create_task(dequeue(self.proc.stdout, self.dialout_ws.send_str))
            await self.dialout_ws.send_str(
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
                        pass
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    break
                try:
                    msg_loaded = json.loads(msg_contents)
                    if msg_loaded.get("commandName") == "sendMessage":
                        self.proc.stdin.write(msg_loaded.encode() + b"\n")
                except Exception as e:
                    print("ws handler got exception", e)
                    pass
            print("done with ws")
        await self.proc.wait()


async def dequeue(stream: asyncio.StreamReader, callback: T.Callable[[bytes], None]):

    while True:
        line = await stream.readline()
        if not line:
            break
        await callback(line.decode())


async def noGet(request):
    raise web.HTTPFound(location="https://signal.org/")


async def get_handler(request):
    account = request.match_info.get("phonenumber")
    session = request.app["sessions"].get(account, None)
    status = ""
    if not session:
        new_session = Session(account, "", pghelper=app["user_manager_connection"])
        request.app["sessions"][account] = new_session
        app.loop.create_task(new_session.launch_and_connect())
        status = "launched"
    else:
        status = str(session)
    return web.json_response({"status": status})


async def send_message_handler(request):
    account = request.match_info.get("phonenumber")
    session = request.app["sessions"].get(account, None)
    msg_data = await request.text()
    recipient = json.loads(msg_data).get("recipient", "+15133278483")
    if session:
        await session.send_message(recipient, msg_data)
    return web.json_response({"status": "sent"})


async def inbound_handler(request):
    msg_data = await request.text()
    # parse query-string encoded sms/mms into object
    msg_obj = {x: y[0] for x, y in urllib.parse.parse_qs(msg_data).items()}
    destination = msg_obj.get("destination")
    ## lookup sms recipient to signal recipient
    recipient = {}.get(destination, "+15133278483")
    # find first user
    for session in request.app["sessions"].values():
        # send hashmap as signal message with newlines and tabs and stuff
        await session.send_message(
            recipient, "\n".join((f"{key}:\t{value}" for key, value in msg_obj.items()))
        )
        return web.Response(text="TY!")


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
                maybe_session = app["sessions"].get(file_to_sync[2:])
                if maybe_session:
                    await maybe_session.put_file()

    app["mem_task"] = asyncio.create_task(background_sync_handler())


async def on_shutdown(app):
    for session in app["sessions"].values():
        try:
            session.proc.kill()
            await session.proc.wait()
            await session.dialout_ws.close(
                code=aiohttp.WSCloseCode.GOING_AWAY, message="Server shutdown"
            )
        except:
            pass
        await session.put_file()
        await session.mark_freed()


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


async def start_pg_user_manager(app):
    async def create_user_manager():
        app["user_manager_connection"] = UserManager()

    asyncio.create_task(create_user_manager())


app = web.Application()

app.on_shutdown.append(on_shutdown)
app.on_startup.append(start_memfs)
app.on_startup.append(start_websocat)
app.on_startup.append(start_queue_monitor)
app.on_startup.append(start_pg_user_manager)

app.add_routes(
    [
        web.get("/", noGet),
        web.post("/user/{phonenumber}", send_message_handler),
        web.get("/user/{phonenumber}", get_handler),
        web.post("/inbound", inbound_handler),
    ]
)

app["sessions"] = {}


if __name__ == "__main__":
    web.run_app(app, port=8080, host="0.0.0.0")
