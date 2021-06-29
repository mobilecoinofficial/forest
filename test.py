#!/usr/bin/python3.9
import cmd

import asyncio
import json
import shlex
import time
import subprocess
from subprocess import PIPE
from typing import Coroutine

# from hypothesis import given, strategies as st

import pytest


import datastore
import utils
import main
from main import Message


class LocalForest:
    def __init__(self):
    # we're communicating exclusively over  http, shouldn't need pipes
        self.get  = subprocess.Popen(["python3.9", "main.py"], stdout=PIPE, stderr=PIPE) # could run it in docker as well but it ought to work locally
        # we need to pass the "webhooks" flag or smth

    def check(self) -> bool:
        if self.proc.poll():
            print(stdout.decode()
            print(stderr.decode()
            return False# test failed
        return True

    def send(self, msg) -> bool:
        if check():
            return False
        test_subject = utils.get_secret("BOT_NUMBER")
        requests.post("http://localhost:8080/send/", data={"recipient": test_subject, "message": msg})


    def recv(self) -> dict:
        if check():
            return False
        return requests.post("https://localhost:8080/next_message", timeout=30).json()

async def test_datastore() -> None:
    local = LocalForest()
    # deploy
    subprocess.run("fly deploy", shell=True)

    # send a message from a different account
    time.sleep(1)
    local.send("/printerfact")
    assert "printer" in Message(local.recv()).text


    # deploy
    # might need to tweak something for fly to redeploy? maybe change a secret?
    subprocess.run("fly deploy", shell=True)
    input("enter when fly has finished deploying")
    # send another message
    local.send("/printerfact") # we want an echo for different ones
    assert "printer" in Message(local.recv()).text

    await sess.send_message(utils.get_secret("BOT_NUMBER"), "/echo ham")
    for msg in await sess.signalcli_output_iter():
        # expect it to work
        assert msg.text == "ham"
        break


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


class RecordSignalTestShell(cmd.Cmd, Session):
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
        Session.__init__(self, "+1" + "5" * 10)
        cmd.Cmd.__init__(self)
        self.loop = asyncio.new_event_loop()
        asyncio.events.set_event_loop(self.loop)
        self.loop.set_debug(True)

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

    async def do_init(self, line: str) -> None:
        await self.launch_and_connect()
        await self.handle_messages()

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
