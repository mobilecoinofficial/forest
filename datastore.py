#!/bin/python3.9
import asyncio
import json
import logging
import os
import shutil
import time
from io import BytesIO
from pathlib import Path
from subprocess import PIPE, Popen
from tarfile import TarFile
from typing import Any, Optional, cast
import argparse

import aioprocessing
from aiohttp import web

import fuse
import mem
import utils
from pghelp import PGExpressions, PGInterface

if utils.get_secret("MIGRATE"):
    get_datastore = "SELECT account, datastore FROM {self.table} WHERE id=$1"
else:
    get_datastore = "SELECT datastore FROM {self.table} WHERE id=$1"


class DatastoreError(Exception):
    pass


AccountPGExpressions = PGExpressions(
    table="signal_accounts",
    # rename="ALTAR TABLE IF EXISTS prod_users RENAME TO {self.table}",
    migrate="ALTER TABLE IF EXISTS {self.table} ADD IF NOT EXISTS datastore BYTEA, \
        ADD IF NOT EXISTS registered BOOL; ",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} \
            (id TEXT PRIMARY KEY, \
            datastore BYTEA, \
            last_update_ms BIGINT, \
            last_claim_ms BIGINT, \
            active_node_name TEXT);",
    is_registered="SELECT datastore is not null as registered FROM {self.table} WHERE id=$1",
    get_datastore=get_datastore,
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
    upload="INSERT INTO {self.table} (id, datastore, last_update_ms) \
            VALUES($1, $2, (extract(epoch from now()) * 1000)) \
            ON CONFLICT (id) DO UPDATE SET \
            datastore = $2, last_update_ms = EXCLUDED.last_update_ms;",
    free_accounts_not_updated_in_the_last_hour="UPDATE {self.table} \
            SET last_claim_ms = 0, active_node_name = NULL \
            WHERE last_update_ms < ((extract(epoch from now())-3600) * 1000);",
    get_timestamp="select last_update_ms from {self.table} where id=$1",
)


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
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logging.error(e)
            return False

    async def is_claimed(self) -> Optional[str]:
        record = await self.account_interface.get_claim(self.number)
        if not record:
            logging.warning("checking claim without plus instead")
            record = await self.account_interface.get_claim(self.number[1::])
            if record:
                return record[0].get("active_node_name")
            raise Exception(f"no record in db for {self.number}")
        return record[0].get("active_node_name")

    async def download(self) -> None:
        """Fetch our account datastore from postgresql and mark it claimed"""
        logging.info("datastore download entered")
        await self.account_interface.free_accounts_not_updated_in_the_last_hour()
        for i in range(5):
            logging.info("checking claim")
            claim = await self.is_claimed()
            if not claim:
                logging.info("no account claim!")
                break
            # you can also try to kill the other process
            logging.info(
                "this account is claimed by %s, waiting",
                claim,
            )
            await asyncio.sleep(6)
            if i == 4:
                logging.info("time's up")
        logging.info("downloading")
        record = await self.account_interface.get_datastore(self.number)
        if not record and utils.get_secret("MIGRATE"):
            logging.warning("trying without plus")
            record = await self.account_interface.get_datastore(
                self.number.removeprefix("+")
            )
        logging.info("got datastore from pg")
        if json_data := record[0].get("account"):
            # legacy json-only field
            loaded_data = json.loads(json_data)
            if "username" in loaded_data:
                try:
                    os.mkdir("data")
                except FileExistsError:
                    pass
                open("data/" + loaded_data["username"], "w").write(json_data)
                return
        buffer = BytesIO(record[0].get("datastore"))
        tarball = TarFile(fileobj=buffer)
        fnames = [member.name for member in tarball.getmembers()]
        logging.debug(fnames[:2])
        logging.info(
            "expected file %s exists: %s",
            self.filepath,
            self.filepath in fnames,
        )
        tarball.extractall(utils.ROOT_DIR)
        # open("last_downloaded_checksum", "w").write(zlib.crc32(buffer.seek(0).read()))
        await self.account_interface.mark_account_claimed(self.number, utils.HOSTNAME)
        logging.debug("marked account as claimed, asserting that this is the case")
        assert await self.is_claimed()
        return

    def tarball_data(self) -> Optional[bytes]:
        if not self.is_registered_locally():
            logging.error("datastore not registered. not uploading")
            return None
        # fixme: check if the last thing we downloaded/uploaded
        # is older than the last thing in the db
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
                "couldn't find %s in %s, adding data instead",
                self.filepath + ".d",
                os.getcwd(),
            )
            tarball.add("data")
        fnames = [member.name for member in tarball.getmembers()]
        logging.debug(fnames[:2])
        tarball.close()
        buffer.seek(0)
        data = buffer.read()
        return data

    async def upload(self) -> Any:
        """Puts account datastore in postgresql."""
        data = self.tarball_data()
        if not data:
            return
        kb = round(len(data) / 1024, 1)
        # maybe something like:
        # upload and return registered timestamp. write timestamp locally. when uploading, check that the last_updated_ts in postgres matches the file
        # if it doesn't, you've probably diverged, but someone may have put an invalid ratchet more recently by mistake (e.g. restarting triggering upload despite crashing)
        # or:
        # open("last_uploaded_checksum", "w").write(zlib.crc32(buffer.seek(0).read()))
        await self.account_interface.upload(self.number, data)
        logging.debug("saved %s kb of tarballed datastore to supabase", kb)
        return

    async def mark_freed(self) -> list:
        """Marks account as freed in PG database."""
        return await self.account_interface.mark_account_freed(self.number)


async def getFreeSignalDatastore() -> SignalDatastore:
    interface = get_account_interface()
    await interface.free_accounts_not_updated_in_the_last_hour()
    record = await interface.get_free_account()
    if not record:
        raise Exception("no free accounts")
        # alternatively, register an account...
    number = record[0].get("id")
    logging.info(number)
    assert number
    return SignalDatastore(number)


_memfs_process = None


async def start_memfs(app: web.Application) -> None:
    """
    mount a filesystem in userspace to store data
    the fs contents are stored in memory, so that our keys never touch a disk
    this means we can log signal-cli's interactions with fs,
    and store them in mem_queue
    """
    # refactor this whole mess into some sort of more general "figure out where we are before downloading"
    if utils.LOCAL:
        if utils.ROOT_DIR == ".":
            logging.warning("not deleting current dir, so not starting memfs")
            return 
        try:
            shutil.rmtree(utils.ROOT_DIR)
        except (FileNotFoundError, OSError) as e:
            logging.warning("couldn't remove rootdir: %s", e)
        os.mkdir(utils.ROOT_DIR)
        os.mkdir(utils.ROOT_DIR + "/data")
        # we're going to be running in the repo
        sigcli = utils.get_secret("SIGNAL_CLI_PATH") or "signal-cli"
        sigcli_path = Path(sigcli).absolute()
        logging.info("symlinking %s to %s", sigcli_path, utils.ROOT_DIR)
        os.symlink(sigcli_path, utils.ROOT_DIR + "/signal-cli")
        os.symlink(Path("avatar.png").absolute(), utils.ROOT_DIR + "/avatar.png")
        logging.info("chdir to %s", utils.ROOT_DIR)
        os.chdir(utils.ROOT_DIR)
        logging.info("not starting memfs because running locally")
        return
    logging.info("starting memfs")
    app["mem_queue"] = mem_queue = aioprocessing.AioQueue()
    if not os.path.exists("/dev/fuse"):
        # you *must* have fuse already loaded if running locally
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
        logging.info("in memfs_proc")
        pid = os.getpid()
        open("/dev/stdout", "w").write(
            f"Starting memfs with PID: {pid} on dir: {path}\n"
        )
        backend = mem.Memory(logqueue=mem_queue)  # type: ignore
        logging.info("initing FUSE")
        return fuse.FUSE(operations=backend, mountpoint=utils.ROOT_DIR + "/data")  # type: ignore

    async def launch() -> None:
        logging.info("about to launch memfs with aioprocessing")
        memfs = aioprocessing.AioProcess(target=memfs_proc)
        memfs.start()  # pylint: disable=no-member
        app["memfs"] = memfs
        _memfs_process = memfs

    logging.info("awaiting launch func")
    await launch()


# input, operation, path, arguments, caller
# ["->", "fsync", "/+14703226669", "(1, 2)", "/app/signal-cli", ["/app/signal-cli", "--config", "/app", "--username=+14703226669", "--output=json", "stdio", ""], 0, 0, 523]
# ["<-", "fsync", "0"]
async def start_memfs_monitor(app: web.Application) -> None:
    """
    monitor the memfs activity queue for file saves, sync with supabase
    """

    async def upload_after_signalcli_writes() -> None:
        queue = app.get("mem_queue")
        if not queue:
            logging.info("no mem_queue, nothing to monitor")
            return
        logging.info("monitoring memfs")
        counter = 0
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
                    counter += 1
                    if time.time() % (60 * 3) == 0:
                        logging.info("background syncs in the past ~3min: %s", counter)
                        counter = 0
                    await maybe_session.datastore.upload()

    app["mem_task"] = asyncio.create_task(upload_after_signalcli_writes())


async def standalone(number: str) -> None:
    app = cast(web.Application, {})
    asyncio.create_task(start_memfs(app))
    await start_memfs_monitor(app)
    try:
        datastore = SignalDatastore(number)
        await datastore.download()
    except (IndexError, DatastoreError):
        datastore = await getFreeSignalDatastore()
        await datastore.download()
    try:
        while 1:
            time.sleep(3600)
    except KeyboardInterrupt:
        await datastore.upload()
        await datastore.mark_freed()


# maybe a config about where we're running
# MEMFS, DOWNLOAD, ROOT_DIR, HOSTNAME, etc
# is HCL overkill?

parser = argparse.ArgumentParser(
    description="manage the signal datastore. use ENV=... ./datastore.py to use something other than dev"
)
subparser = parser.add_subparsers(dest="subparser")  # ?
sync_parser = subparser.add_parser("sync")
sync_parser.add_argument("--number")
upload_parser = subparser.add_parser("upload")
upload_parser.add_argument("--path")
upload_parser.add_argument("--number")
download_parser = subparser.add_parser("download")
download_parser.add_argument("--number")
migrate_parser = subparser.add_parser("migrate")
migrate_parser.add_argument("--create")


async def do_free(ns: argparse.Namespace) -> None:
    await get_account_interface().mark_account_freed(ns.number)


free_parser = subparser.add_parser("free", help="mark account freed")
free_parser.add_argument("--number")
free_parser.set_defaults(func=do_free)


if __name__ == "__main__":
    args = parser.parse_args()
    if hasattr(args, "func"):
        asyncio.run(args.func(args))
    elif args.subparser == "sync":
        asyncio.run(standalone(args.number))
    elif args.subparser == "upload":
        if args.path:
            os.chdir(args.path)
        if args.number:
            num = args.number
        else:
            num = os.listdir("data")[0]
        store = SignalDatastore(num)
        asyncio.run(store.upload())

    else:
        print("not implemented")
