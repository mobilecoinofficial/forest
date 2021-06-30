#!/usr/bin/python3.9
import cmd

import asyncio
import json
import shlex
import time
import subprocess
from subprocess import PIPE
from typing import Coroutine, Optional
import logging
import socket
import pytest
import requests


import datastore
import utils
import main
from main import Message


import filecmp
import os.path

# https://stackoverflow.com/a/6681395
def are_dir_trees_equal(dir1, dir2):
    """
    Compare two directories recursively. Files in each directory are
    assumed to be equal if their names and contents are equal.

    @param dir1: First directory path
    @param dir2: Second directory path

    @return: True if the directory trees are the same and
        there were no errors while accessing the directories or files,
        False otherwise.
    """

    dirs_cmp = filecmp.dircmp(dir1, dir2)
    if (
        len(dirs_cmp.left_only) > 0
        or len(dirs_cmp.right_only) > 0
        or len(dirs_cmp.funny_files) > 0
    ):
        return False
    (_, mismatch, errors) = filecmp.cmpfiles(
        dir1, dir2, dirs_cmp.common_files, shallow=False
    )
    if len(mismatch) > 0 or len(errors) > 0:
        return False
    for common_dir in dirs_cmp.common_dirs:
        new_dir1 = os.path.join(dir1, common_dir)
        new_dir2 = os.path.join(dir2, common_dir)
        if not are_dir_trees_equal(new_dir1, new_dir2):
            return False
    return True


# _accounts= asyncio.run(get_account_interface().async_execute("SELECT id FROM signal_accounts"))
# accounts = [record.get("id") for record in _accounts]

# from hypothesis import given, strategies as st
# @given(st.builds(SignalDatastore, st.sample_from(accounts)):
# async def test_store(store: SignalDatastore, tmpdir_factory: "TempdirFactory") -> None:
#     dir1 = tmpdir_factory.mktemp("dir1")
#     os.chdir(dir1)
#     await store.download()
#     await store.upload()
#     dir2 = tmpdir_factory.mktemp("dir2")
#     os.chdir(dir2)
#     await store.download()
#     assert are_dir_trees_equal(dir1, dir2)

# https://gist.github.com/butla/2d9a4c0f35ea47b7452156c96a4e7b12
def wait_for_port(port, host="0.0.0.0", timeout=120):
    """Wait until a port starts accepting TCP connections.
    Args:
        port (int): Port number.
        host (str): Host address on which the port should exist.
        timeout (float): In seconds. How long to wait before raising errors.
    Raises:
        TimeoutError: The port isn't accepting connection after time specified in `timeout`.
    """
    start_time = time.perf_counter()
    while True:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                logging.info("up after %s s ", time.perf_counter() - start_time)
                break
        except OSError as ex:
            time.sleep(0.01)
            if time.perf_counter() - start_time >= timeout:
                raise TimeoutError(
                    "Waited too long for the port {} on host {} to start accepting "
                    "connections.".format(port, host)
                ) from ex


class LocalForest:
    def __init__(self) -> None:
        self.proc = None
        pass

    def __enter__(self) -> None:

        # we're communicating exclusively over  http, shouldn't need pipes
        self.proc = subprocess.Popen("./main.py +***REMOVED***".split())
        #    "./signal-cli --config /tmp/local-signal --output-format=json stdio"

        logging.info("started proc %s", self.proc)
        return self

    def __exit__(self, _, __, ___) -> None:
        if self.proc:
            self.proc.send_signal(2)  # sigint
            time.sleep(2)
            self.proc.terminate()
            # logging.info("trying to print output")
            # logging.info(self.proc.stdout.read())
            # logging.info(self.proc.stderr.read())

    def check(self) -> bool:
        result = self.proc.poll()
        logging.info(result)
        if self.proc.poll() is not None:
            # logging.info("trying to print output")
            # logging.info(self.proc.stdout.decode())
            # logging.info(self.proc.stderr.decode())
            return False  # test failed
        return True

    def send(self, msg: str, endsession=False) -> bool:
        if not self.check():
            return False
        test_subject = utils.get_secret("BOT_NUMBER")
        data = {
            "recipient": test_subject,
            "message": msg,
            "endsession": endsession,
        }
        requests.post(
            "http://0.0.0.0:8080/user/1",
            data,
            timeout=70,
        )

    def recv(self) -> Optional[dict]:
        if not self.check():
            return None
        resp = requests.get("http://0.0.0.0:8080/next_message", timeout=70)
        logging.info(resp)
        logging.info(resp.json())
        if not isinstance(resp, dict):
            return self.recv()
        return resp.json()


async def test_datastore() -> None:
    with LocalForest() as local:
        # deploy
        #    subprocess.run("fly deploy", shell=True)

        # send a message from a different account
        time.sleep(1)
        wait_for_port(8080)
        local.send("", endsession=True)
        local.send("/printerfact")
        resp = local.recv()
        assert resp
        logging.info(resp)
        msg = Message(resp)
        logging.info(msg)
        text = msg.text
        logging.info(text)
        assert "printer" in text  # Message(local.recv()).text

        # deploy
        # might need to tweak something for fly to redeploy? maybe change a secret?
        subprocess.run("fly deploy", shell=True)
        input("enter when fly has finished deploying")
        # send another message
        local.send("/printerfact")  # we want an echo for different ones
        assert "printer" in Message(local.recv()).text


if __name__ == "__main__":
    asyncio.run(test_datastore())
# class TestSignalInteractionRecording(Session):
#     # idk import the parser that java uses...

#     history: list[dict] = {}
#     intro = "recording test case"
#     prompt = "signal"

#     def emptyline(self) -> None:
#         pass

#     def record(self):
#         while 1:
#             command = input()

#     def do_exec(self, arg):
#         pass


# right now we exclusively want to send and receive signal messages to one number
# in a shell
# i am abstracting over that part only

# register for signal -> set number of test subject ->, then just use ["<-", "->"] like the silly fuse syntax

# later, i want to be able to run "fly deploy" as part of my test of the counterparty datastore


# at the very end, i might use the same framework for more internal tests


class RecordSignalTestShell(cmd.Cmd):
    # copied from https://docs.python.org/3/library/cmd.html#cmd-example
    intro = "record ye test\n"
    prompt = "(signal) "
    log = []
    file = None
    output_file = None
    testing = False
    expected_output_queue = list()
    actual_output = []
    completekey = "tab"
    cmdqueue = []

    def __init__(self) -> None:
        # Session.__init__(self, "+1" + "5" * 10)
        cmd.Cmd.__init__(self)
        self.loop = asyncio.new_event_loop()
        asyncio.events.set_event_loop(self.loop)
        self.loop.set_debug(True)

    async def do_init(self, line: str) -> None:
        """launch and handle signal-cli if we are inheriting from Session"""
        # await self.launch_and_connect()
        # await self.handle_messages()

    def handle_message(self, msg: Message) -> None:
        msg.ts = None
        self.record_output(msg)

    def record_output(self, output) -> None:
        json.dump(output, self.file)
        self.actual_output.append(json.dumps(output))

    # ----- basic turtle commands -----
    # ----- record and playback -----
    def do_record(self, arg: str) -> None:
        "Save future commands to filename:  RECORD rose.cmd"
        self.file = open(arg, "a")
        self.output_file = open(arg + "_output", "a")

    def do_playback(self, arg: str) -> None:
        "Playback commands from a file:  PLAYBACK rose.cmd"
        self.close()
        # this needs to figure out what/how to fuzz and construct a given() call
        self.testing = True
        with open(arg) as f:
            self.cmdqueue.extend(f.read().splitlines())
        with open(arg + "_output") as f:
            self.expected_output_queue.extend(f.read().splitlines())

    def precmd(self, line: str) -> str:
        if self.file and "playback" not in line:
            print(line, file=self.file)
        return line

    def onecmd(self, line):
        if line == "init":
            asyncio.create_task(self.do_init(line))
        result = super().onecmd(line)
        if isinstance(result, Coroutine):
            # ?? switch to cooperative execution in a blocking way? dig into cmd some more?
            self.loop.run_until_complete(result)

    # reveal_type(onecmd)

    def postcmd(self, stop, line) -> None:
        if self.testing:
            assert self.actual_output[-1] == self.expected_output_queue.pop(0)

    def close(self) -> None:
        if self.file:
            self.file.close()
            self.output_file.close()
            self.file = None

    def do_mark_successful(self, arg):
        """
        mark this this session as successful.
        create a test expecting the "same sort of thing" for timing,
        signal commands.
        numbers in teli or signal format are replaced with valid numbers
        [numbers formatted in some specific way are formatted in random
        valid ways as defined [fixme]
        """

    def do_pause(self, delay: int) -> None:
        time.sleep(delay)
        # @given(pause=st.dela

    def do_mark_unsuccessful(self, arg):
        pass

    def do_exec(self, arg: str) -> None:
        proc = subprocess.run(
            shlex.split(arg), shell=True, check=False, stdout=PIPE, stderr=PIPE
        )
        self.record_output((proc.stdout.decode(), proc.stderr.decode()))
        # if self.testing:
        # assert output == example?


#     command_queue: AioQueue
#     output_queue: AioQueue

# if __name__ == "__main__":
#     try:
#         shell = RecordSignalTestShell()
#         shell.cmdloop()
#     finally:
#         try:
#             shell.loop.run_until_complete(loop.shutdown_default_executor())
#             shell.loop.close()
#         except AttributeError:
#             pass


# async def start_console(app: web.Application) -> None:
#     app["console_signal_command_queue"] = command_queue = aioprocessing.AioQueue()
#     app["console_signal_output_queue"] = output_queue = aioprocessing.AioQueue()


#     def console_proc(path: str = "data") -> Any:
#         pid = os.getpid()
#         open("/dev/stdout", "w").write(
#             f"Starting memfs with PID: {pid} on dir: {path}\n"
#         )
#         shell = RecordSignalTestShell(command_queue, output_queue)
#         # later, allow constructing Message objects to pass to handle_messages in addition to legitimate messages
#         shell.cmdloop()

#         backend = mem.Memory(logqueue=mem_queue)  # type: ignore
#         return fuse.FUSE(operations=backend, mountpoint="/app/data")  # type: ignore

#     async def launch() -> None:
#         memfs = aioprocessing.AioProcess(target=memfs_proc)
#         # the process needs to become pid one, sorta
#         # maybe we want to start an async loop in the other process?
#         # we could also just abstract out signal-cli a bit further, have it simply expose signal-cli bindings
#         memfs.start()  # pylint: disable=no-member
#         app["memfs"] = memfs

#     await launch()

# # i'm an idiot
# # i want to make send/receive requests via webhooks
# # get to advance your queue

# async def receive_logs(app:

# # input, operation, path, arguments, caller
# # ["->", "fsync", "/+14703226669", "(1, 2)", "/app/signal-cli", ["/app/signal-cli", "--config", "/app", "--username=+14703226669", "--output=json", "stdio", ""], 0, 0, 523]
# # ["<-", "fsync", "0"]
# async def start_queue_monitor(app: web.Application) -> None:
#     """
#     monitor the memfs activity queue for file saves, sync with supabase
#     """

#     async def read_console_requests():
#         session = app.get("session")
#         if session:
#             queue = app.get("console_signal_command_queue")
#             for cmd in await queue.get():
#                 session.signalcli_input_queue.put(json.dumps(cmd).encode() + b"\n")

#     app["singal_input"] = asyncio.create_task(read_console_requests())


# async def

# async def local_main() -> None:
#     AioProcess(console_thread()
#     main.app
#         runner = web.AppRunner(main.app)
#         await runner.setup()
#         site = web.TCPSite(runner, "0.0.0.0", self.port)
#         logging.info("starting SMS receiving server")
#         try:
#             await site.start()
#             yield site
#         finally:
#             await self.app.shutdown()
#             await self.app.cleanup()
