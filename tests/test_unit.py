import asyncio
import logging
import os
import pathlib
from importlib import reload
from typing import Optional
import pytest

# Prevent Utils from importing dev_secrets by default
os.environ["ENV"] = "test"

from forest import utils
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

    async def get_output(self) -> str:
        """Runs commands as normal but intercepts the output instead of passing it onto signal"""
        try:
            outgoing_msg = await asyncio.wait_for(self.outbox.get(), timeout=1)
            return outgoing_msg["params"]["message"]
        except asyncio.TimeoutError:
            logging.error("timed out waiting for output")
            return ""

    async def get_cmd_output(self, text: str) -> str:
        """Runs commands as normal but intercepts the output instead of passing it onto signal"""
        await self.inbox.put(MockMessage(text))
        return await self.get_output()


@pytest.mark.asyncio
async def test_commands() -> None:
    """Tests commands"""
    bot = MockBot(BOT_NUMBER)

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

    print("come on man")
    print(await bot.get_cmd_output("/help"))

    assert (await bot.get_cmd_output("/help")).startswith("Documented commands:")

    # test the default behaviour
    assert (await bot.get_cmd_output("gibberish two")).startswith(
        "That didn't look like a valid command"
    )


@pytest.mark.asyncio
async def test_questions() -> None:
    """Tests the various questions from questionbot class"""
    bot = MockBot(BOT_NUMBER)

    # Enable Magic allows for mistyped commands
    os.environ["ENABLE_MAGIC"] = "1"

    assert await bot.get_cmd_output("test_ask_yesno_question") == "Do you like faeries?"
