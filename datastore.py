import asyncio
import json
import logging
import os
import shutil
from io import BytesIO
from pathlib import Path
from subprocess import PIPE, Popen
from tarfile import TarFile
from typing import Any, Optional

import aioprocessing
import requests
from aiohttp import web

import fuse
import mem
import utils
from pghelp import PGExpressions, PGInterface

# maybe we'd like nicknames for accounts?

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
    upload="UPDATE {self.table} SET \
            datastore = $2, \
            len_keys = $3, \
            registered = $4, \
            last_update_ms = (extract(epoch from now()) * 1000) \
            WHERE id=$1;",
    create_account="INSERT INTO {self.table} (id, datastore) \
            VALUES($1, $2) ON CONFLICT DO NOTHING;",
    free_accounts_not_updated_in_the_last_hour="UPDATE {self.table} \
            SET last_claim_ms = 0, active_node_name = NULL \
            WHERE last_update_ms < ((extract(epoch from now())-3600) * 1000);",
)

# migration strategy:
# backwards compat with non-tar datastore


def get_account_interface() -> PGInterface:
    return PGInterface(
        query_strings=AccountPGExpressions,
        database=utils.get_secret("DATABASE_URL"),
    )


class SignalDatastore:
    """
    Download, claim, mount, and sync a signal datastore
    """

    def __init__(self, number: str):
        self.account_interface = get_account_interface()
        self.number = utils.signal_format(number)
        logging.info("SignalDatastore number is %s", self.number)
        self.filepath = "data/" + number
        # await self.account_interface.create_table()

    def is_registered_locally(self) -> bool:
        try:
            return json.load(open(self.filepath))["registered"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return False

    async def is_registered_in_db(self) -> bool:
        record = await self.account_interface.is_registered(self.number)
        if not record:
            return False
        return bool(record[0].get("registered"))

    async def is_claimed(self) -> Optional[str]:
        record = await self.account_interface.get_claim(self.number)
        if not record:
            raise Exception(f"no record in db for {self.number}")
        return record[0].get("active_node_name")

    async def download(self, terminate: bool = True) -> None:
        """Fetch our account datastore from postgresql and mark it claimed
        try terminating the old process by default"""
        tried_to_terminate = False
        for i in range(5):
            claim = await self.is_claimed()
            if not claim:
                break
            if terminate and not tried_to_terminate:
                logging.info(
                    "this account is claimed, waiting and trying to terminate %s",
                    claim,
                )
                try:
                    resp = requests.post(utils.URL + "/terminate", data=claim)
                    logging.info("got %s", resp.text)
                except (requests.exceptions.RequestException, ConnectionError):
                    pass
                tried_to_terminate = True
            await asyncio.sleep(6)
            if i == 9:
                logging.info("30s is up, downloading anyway")
        record = await self.account_interface.get_datastore(self.number)
        buffer = BytesIO(record[0].get("datastore"))
        tarball = TarFile(fileobj=buffer)
        fnames = [member.name for member in tarball.getmembers()]
        logging.info(fnames)
        logging.info(
            f"expected file %s exists: %s",
            self.filepath,
            self.filepath in fnames,
        )
        tarball.extractall(utils.ROOT_DIR)
        await self.account_interface.mark_account_claimed(
            self.number, utils.HOSTNAME
        )
        assert await self.is_claimed()
        await self.account_interface.get_datastore(self.number)

    async def upload(self, create: bool = False) -> Any:
        """Puts account datastore in postgresql."""
        if not self.is_registered_locally():
            logging.error("datastore not registered. not uploading")
            return
        buffer = BytesIO()
        tarball = TarFile(fileobj=buffer, mode="w")
        try:
            tarball.add(self.filepath)
            try:
                tarball.add(self.filepath + ".d")
            except FileNotFoundError:
                logging.info("ignoring no %s", self.filepath + ".d")
        except FileNotFoundError:
            logging.warning(
                f"couldn't find {self.filepath}.d in {os.getcwd()}, adding data instead"
            )
            tarball.add("data")
        print(tarball.getmembers())
        tarball.close()
        buffer.seek(0)
        data = buffer.read()
        kb = round(len(data) / 1024, 1)
        if create:
            result = await self.account_interface.create_account(
                self.number, data
            )
        else:
            len_keys = len(
                json.load(open(self.filepath))["axolotlStore"]["preKeys"]
            )
            result = await self.account_interface.upload(
                self.number, data, len_keys, self.is_registered_locally()
            )
        logging.info("upload query result %s", result)
        logging.info(f"saved {kb} kb of tarballed datastore to supabase")
        return


async def getFreeSignalDatastore() -> SignalDatastore:
    record = await get_account_interface().get_free_account()
    if not record:
        raise Exception("no free accounts")
        # alternatively, register an account...
    number = record[0].get("id")
    return SignalDatastore(number)


async def start_memfs(app: web.Application) -> None:
    """
    mount a filesystem in userspace to store data
    the fs contents are stored in memory, so that our keys never touch a disk
    this means we can log signal-cli's interactions with fs,
    and store them in mem_queue
    """
    app["mem_queue"] = mem_queue = aioprocessing.AioQueue()
    if utils.LOCAL:
        try:
            shutil.rmtree(utils.ROOT_DIR)
        except (FileNotFoundError, OSError) as e:
            logging.warning("couldn't remove rootdir: %s", e)
        os.mkdir(utils.ROOT_DIR)
        os.mkdir(utils.ROOT_DIR + "/data")
        # we're going to be running in the repo
        os.symlink(Path("signal-cli").absolute(), utils.ROOT_DIR + "/signal-cli")
        os.chdir(utils.ROOT_DIR)
        return 
    if not os.path.exists("/dev/fuse"):
        # you *must* have fuse already loaded locally
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
        return fuse.FUSE(operations=backend, mountpoint=utils.ROOT_DIR + "/data")  # type: ignore

    async def launch() -> None:
        memfs = aioprocessing.AioProcess(target=memfs_proc)
        memfs.start()  # pylint: disable=no-member
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
        logging.info("monitoring memfs")
        while True:
            queue_item = await queue.coro_get()
            # iff fsync triggered by signal-cli
            if (
                queue_item[0:2] == ["->", "fsync"]
                and queue_item[5][0] == utils.ROOT_DIR + "/signal-cli"
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
            if session.proc:
                session.proc.kill()
                await session.proc.wait()
        except ProcessLookupError:
            pass
        await session.datastore.upload()
        await session.datastore.account_interface.mark_account_freed(
            session.datastore.number
        )
    logging.info("=============exited===================")
