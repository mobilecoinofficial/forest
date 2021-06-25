from typing import Any
from subprocess import Popen, PIPE
from tarfile import TarFile
from io import BytesIO
import os
import logging
import asyncio
from aiohttp import web
import aioprocessing
import fuse
import mem
import utils
from pghelp import PGExpressions, PGInterface

# from base64 import urlsafe_b64encode, urlsafe_b64decode

AccountPGExpressions = PGExpressions(
    table="signal_accounts",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} \
            (id TEXT PRIMARY KEY, \
            datastore BYTEA, \
            last_update_ms BIGINT, \
            last_claim_ms BIGINT, \
            active_node_name TEXT);",
    is_registered="SELECT datastore is not null as registered FROM {self.table} WHERE id=$1",
    get_datastore="SELECT datastore FROM {self.table} WHERE id=$1;",  # AND
    get_claim="SELECT active_node_name FROM {self.table} WHERE id=$1",
    mark_account_claimed="UPDATE {self.table} \
        SET active_node_name = $2, \
        last_claim_ms = (extract(epoch from now()) * 1000) \
        WHERE id=$1;",
    mark_account_freed="UPDATE {self.table} SET last_claim_ms = 0, \
        active_node_name = NULL WHERE id=$1;",
    get_free_account="SELECT (id, datastore) FROM {self.table} \
            WHERE active_node_name IS NULL \
            AND last_claim_ms = 0 \
            LIMIT 1;",
    # mark_account_updated="UPDATE {self.table} SET \
    #     last_update_ms = (extract(epoch from now()) * 1000) \
    #     WHERE id=$1;",
    upload="UPDATE {self.table} SET \
            datastore = $2, \
            last_update_ms = (extract(epoch from now()) * 1000) \
            WHERE id=$1;",
    create_account="INSERT INTO {self.table} (id, datastore) \
            VALUES($1, $2) ON CONFLICT DO NOTHING;",
    free_accounts_not_updated_in_the_last_hour="UPDATE {self.table} \
            SET last_claim_ms = 0, active_node_name = NULL \
            WHERE last_update_ms < ((extract(epoch from now())-3600) * 1000);",
)


def get_account_interface() -> PGInterface:
    return PGInterface(
        query_strings=AccountPGExpressions,
        database=utils.get_secret("DATABASE_URL"),
    )


def trueprint(*args: Any, **kwargs: Any) -> None:
    print(*args, **kwargs, file=open("/dev/stdout", "w"))


class SignalDatastore:
    """
    Download, claim, mount, and sync a signal datastore
    """

    def __init__(self, number: str):
        logging.info(number)
        self.account_interface = get_account_interface()
        self.number = utils.signal_format(number)
        logging.info(self.number)
        self.filepath = "data/" + number
        # await self.account_interface.create_table()

    async def is_registered(self) -> bool:
        record = await self.account_interface.is_registered(self.number)
        if not record:
            return False
        return bool(record[0].get("registered"))

    async def is_claimed(self) -> bool:
        record = await self.account_interface.get_claim(self.number)
        if not record:
            raise Exception(f"no record in db for {self.number}")
        return record[0].get("active_node_name") is not  None

    async def download(self) -> None:
        """Fetch our account datastore from postgresql and mark it claimed"""
        for i in range(10):
            if not await self.is_claimed():
                break
            trueprint("this account is claimed, waiting...")
            await asyncio.sleep(6)
            if i == 9:
                trueprint("a minute is up, downloading anyway")
        record = await self.account_interface.get_datastore(self.number)
        buffer = BytesIO(record[0].get("datastore"))
        tarball = TarFile(fileobj=buffer)
        expected = f"data/{self.number}"
        fnames = [member.name for member in tarball.getmembers()]
        trueprint(fnames)
        trueprint(f"expected file {expected} exists:", expected in fnames)
        tarball.extractall("/app")
        await self.account_interface.mark_account_claimed(
            self.number, open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID
        )
        assert await self.is_claimed()
        await self.account_interface.get_datastore(self.number)

    # async def mark_freed(self) -> Any:
    #     """Marks account as freed in PG database."""
    #     return await self.account_interface.mark_account_freed(self.number)

    async def upload(self, create: bool = False) -> Any:
        """Puts account datastore in postgresql."""
        buffer = BytesIO()
        tarball = TarFile(fileobj=buffer, mode="w")
        # os.chdir("/app")
        try:
            tarball.add(f"data/+{self.number}")
            tarball.add(f"data/+{self.number}.d")
        except FileNotFoundError:
            tarball.add("data")
        print(tarball.getmembers())
        tarball.close()
        buffer.seek(0)
        data = buffer.read()
        kb = round(len(data) / 1024)
        if create:
            result = await self.account_interface.create_account(
                self.number, data
            )
        else:
            result = await self.account_interface.upload(self.number, data)
        trueprint(result)
        trueprint(f"saved {kb} kb of tarballed datastore to supabase")


async def start_memfs(app: web.Application) -> None:
    """
    mount a filesystem in userspace to store data
    the fs contents are stored in memory, so that our keys never touch a disk
    this means we can log signal-cli's interactions with fs,
    and store them in mem_queue
    """
    app["mem_queue"] = mem_queue = aioprocessing.AioQueue()
    if utils.LOCAL:
        Popen("sudo mkdir /app".split())
        Popen("sudo chmod 777 /app".split())
        Popen("ln -s ( readlink -f ./signal-cli ) /app/signal-cli".split())
        return
    if not os.path.exists("/dev/fuse"):
        proc = Popen(
            ["/usr/sbin/insmod", "/app/fuse.ko"],
            stdout=PIPE,
            stderr=PIPE,
        )
        proc.wait()
        (stdout, stderr) = proc.communicate()  # pylint: disable=unused-variable
        if stderr:
            raise Exception(
                f"Could not load fuse module! You may need to recompile.\t\n{stderr.decode()}"
            )

    def memfs_proc(path: str = "data") -> Any:
        pid = os.getpid()
        open("/dev/stdout", "w").write(
            f"Starting memfs with PID: {pid} on dir: {path}\n"
        )
        backend = mem.Memory(logqueue=mem_queue)  # type: ignore
        return fuse.FUSE(operations=backend, mountpoint="/app/data")  # type: ignore

    async def launch() -> None:
        memfs = aioprocessing.AioProcess(target=memfs_proc)
        memfs.start() # pylint: disable=no-member
        app["memfs"] = memfs

    await launch()


# input, operation, path, arguments, caller
# ["->", "fsync", "/+14703226669", "(1, 2)", "/app/signal-cli", ["/app/signal-cli", "--config", "/app", "--username=+14703226669", "--output=json", "stdio", ""], 0, 0, 523]
# ["<-", "fsync", "0"]
async def start_queue_monitor(app: web.Application) -> None:
    """
    monitor the memfs activity queue for file saves, sync with supabase
    """

    async def background_sync_handler() -> None:
        queue = app["mem_queue"]
        trueprint("monitoring memfs")
        while True:
            queue_item = await queue.coro_get()
            # iff fsync triggered by signal-cli
            if (
                queue_item[0:2] == ["->", "fsync"]
                and queue_item[5][0] == "/app/signal-cli"
            ):
                # /+14703226669
                # file_to_sync = queue_item[2]
                # 14703226669
                maybe_session = app.get("session")
                if maybe_session:
                    await maybe_session.datastore.put_file()

    app["mem_task"] = asyncio.create_task(background_sync_handler())


async def on_shutdown(app: web.Application) -> None:
    session = app.get("session")
    if session:
        await session.datastore.upload()
        try:
            session.proc.kill()
            await session.proc.wait()
        except ProcessLookupError:
            pass
        await session.datastore.upload()
        await session.datastore.account_interface.mark_account_freed(
            session.datastore.number
        )
    trueprint("=============exited===================")
