import pytest
import datastore
import subprocess
from hypothesis import given, strategies as st

async def test_datastore() -> None
    # deploy
    subprocess.run("fly deploy", shell=True)
    sess = main.Session(bot_number=None)
    await sess.launch_and_connect()
    # send a message from a different account
    await sess.send_message(utils.get_secret("BOT_NUMBER"), "/echo spam"))
    for msg in await sess.signalcli_output_iter():
        assert msg.text = "spam"
        break
    # deploy
    # might need to tweak something for fly to redeploy? maybe change a secret?
    subprocess.run("fly deploy", shell=True)
    # send another message
    await sess.send_message(utils.get_secret("BOT_NUMBER"), "/echo ham"))
    for msg in await sess.signalcli_output_iter():
        # expect it to work
        assert msg.text = "ham"
        break


class TestSignalInteractionRecording(Session):
    # idk import the parser that java uses...

    history: list[dict] = {}
    intro = "recording test case"
    prompt = "signal" 

    def emptyline() -> None:
        pass


    def record():
        while 1:
            cmd = input()

    def do_exec(self, arg):

import cmd, sys

class RecordSignalTestShell(cmd.Cmd):
    # copied from https://docs.python.org/3/library/cmd.html#cmd-example
    intro = 'record ye test\n'
    prompt = '(signal) '
    log = []
    file = None

    def handle_message(self, msg: Message):
        msg.ts = None
        self.record_output(msg)

    def record_output(self, output) -> None:
        json.dump(output, self.file)


    # ----- basic turtle commands -----
    # ----- record and playback -----
    def do_record(self, arg):
        'Save future commands to filename:  RECORD rose.cmd'
        self.file = open(arg, 'a')


    def do_playback(self, arg):
        'Playback commands from a file:  PLAYBACK rose.cmd'
        self.close()
        # this needs to figure out what/how to fuzz and construct a given() call
        line.split 
        with open(arg) as f:
            self.cmdqueue.extend(f.read().splitlines())

    def precmd(self, line):

        if self.file and 'playback' not in line:

        #msg foo register
        #
            print(line, file=self.file)
        return line

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def do_mark_successful(self, cmd):
        """
        mark this this session as successful.
        create a test expecting the "same sort of thing" for timing, signal commands.
        numbers in teli or signal format are replaced with valid numbers
        [numbers formatted in some specific way are formatted in random valid ways as defined [fixme]]
        """
        pass


    def do_pause(self, delay: int):
        time.sleep(delay)
            # @given(pause=st.dela

    def do_mark_unsuccessful(self, cmd):
        pass

    def do_exec(self, cmd):
        proc = subprocess.run(shlex.split(cmd), shell=True, check=False, stdout=PIPE, stderr=PIPE)
        self.record_output(stdout, stderr)
        #if self.testing:
            #assert output == example?



def parse(arg):
    'Convert a series of zero or more numbers to an argument tuple'
    return tuple(map(int, arg.split()))
