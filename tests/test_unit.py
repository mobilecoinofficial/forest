import asyncio
import logging
import os
import pathlib
from importlib import reload
import pytest
import pytest_asyncio

# Prevent Utils from importing dev_secrets by default
os.environ["ENV"] = "test"

from forest import utils, core
from forest.core import Message, QuestionBot, Response

# Sample bot number alice
BOT_NUMBER = "+11111111111"
USER_NUMBER = "+22222222222"


def test_secrets(tmp_path: pathlib.Path) -> None:
    """Tests that utils.get_secret reads the values of dev_secrets properly"""
    open(tmp_path / "test_secrets", "w", encoding="utf-8").write("A=B\nC=D")
    os.chdir(tmp_path)
    reload(utils)

    assert utils.get_secret("A") == "B"
    assert utils.get_secret("C") == "D"
    assert utils.get_secret("E") == ""


def test_root(tmp_path: pathlib.Path) -> None:
    """Tests the Root Dir Logic"""
    os.chdir(tmp_path)

    # Test that ROOT_DIR is . when running locally
    assert reload(utils).ROOT_DIR == "."

    # Test that when Download is set to 1 and so downloading datastore from postgress,
    # the Root Dir is /tmp/local-signal
    open(tmp_path / "test_secrets", "w", encoding="utf-8").write("DOWNLOAD=1")
    assert reload(utils).ROOT_DIR == "/tmp/local-signal"

    # Tests that when a Fly App Name is specified, therefore it must be running on fly,
    # the Root Dir is /app
    os.environ["FLY_APP_NAME"] = "A"
    assert reload(utils).ROOT_DIR == "/app"


class MockMessage(Message):
    """Makes a Mock Message that has a predefined source and uuid"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.source = USER_NUMBER
        self.uuid = "cf3d7d34-2dcd-4fcd-b193-cbc6a666758b"
        self.mentions = []
        super().__init__({})


class MockBot(QuestionBot):
    """Makes a bot that bypasses the normal start_process allowing
    us to have an inbox and outbox that doesn't depend on Signal"""

    async def start_process(self) -> None:
        pass

    async def do_test_ask_yesno_question(self, message: Message) -> Response:
        """Asks a sample Yes or No question"""

        if await self.ask_yesno_question(message.source, "Do you like faeries?"):
            return "That's cool, me too!"
        return "Aww :c"

    # async def send_message(
    #     self,
    #     recipient: Optional[str],
    #     msg: Response,
    #     group: Optional[str] = None,
    #     endsession: bool = False,
    #     attachments: Optional[list[str]] = None,
    #     content: str = "",
    # ) -> str:
    #     return msg

    async def send_input(self, text: str) -> None:
        """Puts a MockMessage in the inbox queue"""
        await self.inbox.put(MockMessage(text))

    async def get_output(self) -> str:
        """Reads messages in the outbox that would otherwise be sent over signal"""
        try:
            outgoing_msg = await asyncio.wait_for(self.outbox.get(), timeout=1)
            return outgoing_msg["params"]["message"]
        except asyncio.TimeoutError:
            logging.error("timed out waiting for output")
            return ""

    async def get_cmd_output(self, text: str) -> str:
        """Runs commands as normal but intercepts the output instead of passing it onto signal"""
        await self.send_input(text)
        return await self.get_output()


# https://github.com/pytest-dev/pytest-asyncio/issues/68
# all async tests and fixtures implicitly use event_loop, which has scope "function" by default
# so if we want bot to have scope "session" (so it's not destroyed and created between tests),
# all the fixtures it uses need to have at least "session" scope
@pytest.fixture(scope="session")
def event_loop(request):
    """special version of the even loop"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def bot():
    """special bot"""
    bot = MockBot(BOT_NUMBER)
    yield bot
    bot.sigints += 1
    bot.exiting = True
    bot.handle_messages_task.cancel()
    await bot.client_session.close()
    await core.pghelp.close_pools()


@pytest.mark.asyncio
async def test_commands(bot) -> None:
    """Tests commands"""
    # Enable Magic allows for mistyped commands
    os.environ["ENABLE_MAGIC"] = "1"

    # Tests do_ping with a mistyped command, expecting "/pong foo"
    assert await bot.get_cmd_output("/pingg foo") == "/pong foo"

    # tests the uptime command just checks to see if it starts with "Uptime: "
    assert (await bot.get_cmd_output("uptime")).startswith("Uptime: ")

    # tests that eval only works for admins
    assert (
        await bot.get_cmd_output("/eval 1+1")
    ) == "you must be an admin to use this command"

    print(await bot.get_cmd_output("/help"))

    assert (await bot.get_cmd_output("/help")).startswith("Documented commands:")

    # test the default behaviour
    assert (await bot.get_cmd_output("gibberish two")).startswith(
        "That didn't look like a valid command"
    )


@pytest.mark.asyncio
async def test_questions(bot) -> None:
    """Tests the various questions from questionbot class"""

    # Enable Magic allows for mistyped commands
    os.environ["ENABLE_MAGIC"] = "1"
    # the issue here is that we need to send "yes" *after* the question has been asked
    # so we make it as create_task, then send the input, then await the task to get the result
    t = asyncio.create_task(bot.ask_yesno_question(USER_NUMBER, "Do you like faeries?"))
    await bot.send_input("yes")
    assert await t == True
