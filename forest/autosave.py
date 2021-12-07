#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
import logging
import os
import time
from pathlib import Path
from subprocess import PIPE, Popen
from typing import Any

import aioprocessing
from aiohttp import web

from forest import fuse, mem, utils

_memfs_process = None


# this is the first thing that runs on aiohttp app startup, before datastore.download
async def start_memfs(app: web.Application) -> None:
    """
    mount a filesystem in userspace to store data
    the fs contents are stored in memory, so that our keys never touch a disk
    this means we can log signal-cli's interactions with fs,
    and store them in mem_queue.
    """
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
        """Start the memfs process"""
        mountpath = Path(utils.ROOT_DIR) / path
        logging.info("Starting memfs with PID: %s on dir: %s", os.getpid(), mountpath)
        backend = mem.Memory(logqueue=mem_queue)  # type: ignore
        logging.info("mountpoint already exists: %s", mountpath.exists())
        Path(utils.ROOT_DIR).mkdir(exist_ok=True, parents=True)
        return fuse.FUSE(operations=backend, mountpoint=utils.ROOT_DIR + "/data")  # type: ignore

    async def launch() -> None:
        logging.info("about to launch memfs with aioprocessing")
        memfs = aioprocessing.AioProcess(target=memfs_proc)
        memfs.start()  # pylint: disable=no-member
        app["memfs"] = memfs
        _memfs_process = memfs

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
