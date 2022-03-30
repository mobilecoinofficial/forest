#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import argparse
import asyncio
import json
import logging
import os
import shutil
import socket
import sys
import time
import datetime
import zoneinfo
from io import BytesIO
from pathlib import Path
from tarfile import TarFile
from typing import Any, Callable, Optional

try:
    # normally in a package
    from forest import pghelp, utils
except ImportError:
    # maybe we're local?
    try:
        import pghelp  # type: ignore
        import utils  # type: ignore
    except ImportError:
        # i wasn't asking
        sys.path.append("forest")
        sys.path.append("..")
        import pghelp  # type: ignore # pylint: disable=ungrouped-imports
        import utils  # type: ignore # pylint: disable=ungrouped-imports
if utils.get_secret("MIGRATE"):
    get_datastore = "SELECT account, datastore FROM {self.table} WHERE id=$1"
else:
    get_datastore = "SELECT datastore FROM {self.table} WHERE id=$1"


class DatastoreError(Exception):
    pass


AccountPGExpressions = pghelp.PGExpressions(
    table="signal_accounts",
    # rename="ALTAR TABLE IF EXISTS prod_users RENAME TO {self.table}",
    migrate="""ALTER TABLE IF EXISTS {self.table} ADD IF NOT EXISTS datastore BYTEA, ADD IF NOT EXISTS notes TEXT""",
    create_table="CREATE TABLE IF NOT EXISTS {self.table} \
            (id TEXT PRIMARY KEY, \
            datastore BYTEA, \
            last_update_ms BIGINT, \
            last_claim_ms BIGINT, \
            active_node_name TEXT, \
            last_node_name TEXT, \
            notes TEXT);",
    is_registered="SELECT datastore is not null as registered FROM {self.table} WHERE id=$1",
    get_datastore=get_datastore,
    get_claim="SELECT active_node_name FROM {self.table} WHERE id=$1",
    mark_account_claimed="UPDATE {self.table} \
        SET active_node_name = $2, \
        last_node_name = $2, \
        last_claim_ms = (extract(epoch from now()) * 1000) \
        WHERE id=$1;",
    mark_account_freed="UPDATE {self.table} SET last_claim_ms = 0, \
        active_node_name = NULL WHERE id=$1;",
    get_free_account="SELECT id, datastore FROM {self.table} \
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


def get_account_interface() -> pghelp.PGInterface:
    return pghelp.PGInterface(
        query_strings=AccountPGExpressions,
        database=utils.get_secret("DATABASE_URL"),
    )


class SignalDatastore:
    """
    Download, claim, mount, and sync a signal datastore
    """

    def __init__(self, number: str):
        self.account_interface = get_account_interface()
        if utils.get_secret("IGNORE_FORMAT"):
            self.number = number
        else:
            formatted_number = utils.signal_format(number)
            if isinstance(formatted_number, str):
                self.number = formatted_number
            else:
                raise Exception("not a valid number")
        logging.info("SignalDatastore number is %s", self.number)
        self.filepath = "data/" + number.split("_")[0]
        # await self.account_interface.create_table()
        if not utils.get_secret("NO_TMPDIR"):
            setup_tmpdir()  # shouldn't do anything if not running locally

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
            record = await self.account_interface.get_claim(self.number[1:])
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
        app_prefix = utils.APP_NAME + "-" if utils.APP_NAME else ""
        node_name = app_prefix + socket.gethostname()
        await self.account_interface.mark_account_claimed(self.number, node_name)
        logging.debug("marked account as claimed, asserting that this is the case")
        assert await self.is_claimed()
        return

    def tarball_data(self) -> Optional[bytes]:
        """Tarball our data files"""
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
        # you could formalize this as "present the previous checksum to upload" as a db procedure
        await self.account_interface.upload(self.number, data)
        logging.debug("saved %s kb of tarballed datastore to supabase", kb)
        return

    async def mark_freed(self) -> list:
        """Marks account as freed in PG database."""
        return await self.account_interface.mark_account_freed(self.number)


def setup_tmpdir() -> None:
    logging.info("setup tmpdir")
    if not utils.LOCAL:
        logging.warning("not setting up tmpdir, FLY_APP_NAME is set")
        return
    if utils.ROOT_DIR == ".":
        logging.warning("not setting up tmpdir, using current directory")
        return
    if utils.ROOT_DIR == "/tmp/local-signal/" and not utils.MEMFS:
        try:
            shutil.rmtree(utils.ROOT_DIR)
        except (FileNotFoundError, OSError) as e:
            logging.warning("couldn't remove rootdir: %s", e)
    Path(utils.ROOT_DIR).mkdir(exist_ok=True, parents=True)
    if not utils.MEMFS:
        (Path(utils.ROOT_DIR) / "data").mkdir(exist_ok=True, parents=True)
    try:
        os.symlink(Path("avatar.png").absolute(), utils.ROOT_DIR + "/avatar.png")
    except FileExistsError:
        pass
    logging.info("chdir to %s", utils.ROOT_DIR)
    os.chdir(utils.ROOT_DIR)
    return


async def getFreeSignalDatastore() -> SignalDatastore:
    interface = get_account_interface()
    await interface.free_accounts_not_updated_in_the_last_hour()
    record = await interface.get_free_account()
    if not record:
        raise Exception("no free accounts")
        # alternatively, register an account...
        # could put some of register.py/signalcaptcha handler here...
    number = record[0].get("id")
    logging.info(number)
    assert number
    return SignalDatastore(number)


# maybe a config about where we're running:
# MEMFS, DOWNLOAD, ROOT_DIR, etc
# is HCL overkill?

parser = argparse.ArgumentParser(
    description="manage the signal datastore. use ENV=... to use something other than dev"
)
subparser = parser.add_subparsers(dest="subparser")  # ?

# h/t https://gist.github.com/mivade/384c2c41c3a29c637cb6c603d4197f9f


def argument(*name_or_flags: Any, **kwargs: Any) -> tuple:
    """Convenience function to properly format arguments to pass to the
    subcommand decorator.
    """
    return (list(name_or_flags), kwargs)


def subcommand(
    _args: Optional[list] = None, parent: argparse._SubParsersAction = subparser
) -> Callable:
    """Decorator to define a new subcommand in a sanity-preserving way.
    The function will be stored in the ``func`` variable when the parser
    parses arguments so that it can be called directly like so::
        args = cli.parse_args()
        args.func(args)
    Usage example::
        @subcommand([argument("-d", help="Enable debug mode", action="store_true")])
        def subcommand(args):
            print(args)
    Then on the command line::
        $ python cli.py subcommand -d
    """

    def decorator(func: Callable) -> None:
        _parser = parent.add_parser(func.__name__, description=func.__doc__)
        for arg in _args if _args else []:
            _parser.add_argument(*arg[0], **arg[1])
        _parser.set_defaults(func=func)

    return decorator


def format_field(field: Any) -> str:
    if isinstance(field, int):
        return (
            datetime.datetime.fromtimestamp(field / 1000)
            .astimezone(zoneinfo.ZoneInfo("localtime"))
            .isoformat(timespec="seconds")
        )
    return str(field)


@subcommand()
async def list_accounts(_args: argparse.Namespace) -> None:
    "list available accounts in table format"
    cols = [
        "id",
        "last_update_ms",
        "last_claim_ms",
        "active_node_name",
        "last_node_name",
    ]
    interface = get_account_interface()
    # sorry
    if "notes" in [
        column.get("column_name")
        for column in (
            await interface.execute(
                "select column_name from information_schema.columns where table_name='signal_accounts';"
            )
            or []  # don't error if the query fails
        )
    ]:
        cols.append("notes")
    query = (
        f"select {' ,'.join(cols)} from signal_accounts order by last_update_ms desc"
    )
    accounts = await get_account_interface().execute(query)
    if not isinstance(accounts, list):
        return
    table = [cols] + [
        [format_field(value) for value in account.values()] for account in accounts
    ]
    str_widths = [max(len(row[index]) for row in table) for index in range(len(cols))]
    row_format = " ".join("{:<" + str(width) + "}" for width in str_widths)
    for row in table:
        print((row_format.format(*row).rstrip()))
    return


@subcommand([argument("--number")])
async def free(ns: argparse.Namespace) -> None:
    "mark account freed"
    await get_account_interface().mark_account_freed(ns.number)


async def _set_note(number: str, note: str) -> None:
    await get_account_interface().execute(
        "update signal_accounts set notes=$1 where id=$2",
        note,
        number,
    )


@subcommand([argument("--number"), argument("note", help="new note for number")])
async def set_note(ns: argparse.Namespace) -> None:
    "set the note field for a number"
    await _set_note(ns.number, ns.note)


@subcommand(
    [argument("--path"), argument("--number"), argument("note", help="note for number")]
)
async def upload(ns: argparse.Namespace) -> None:
    """
    upload a datastore
    --path to a directory that contains data/
    --number for the account number
    note: note indicating if this number is free or used for a specific bot
    """
    if ns.path:
        os.chdir(ns.path)
    if ns.number:
        num = ns.number
    else:
        num = sorted(os.listdir("data"))[0]
    store = SignalDatastore(num)
    await store.account_interface.create_table()
    await store.upload()
    await _set_note(num, ns.note)


@subcommand([argument("--number")])
async def sync(ns: argparse.Namespace) -> None:
    # maybe worth running autosave after all?
    try:
        datastore = SignalDatastore(ns.number)
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


# download_parser = subparser.add_parser("download")
# download_parser.add_argument("--number")
# migrate_parser = subparser.add_parser("migrate")
# migrate_parser.add_argument("--create")


if __name__ == "__main__":
    args = parser.parse_args()
    if hasattr(args, "func"):
        asyncio.run(args.func(args))
    else:
        print("not implemented")
