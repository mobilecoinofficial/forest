import asyncio
import os
import pathlib
from importlib import reload
import pytest

# Prevent Utils from importing dev_secrets by default
os.environ["ENV"] = "test"

from forest import utils
from forest.core import Message, QuestionBot

# Sample bot number alice
BOT_NUMBER = "+11111111111"
USER_NUMBER = "+22222222222"


def test_secrets(tmp_path: pathlib.Path) -> None:
    """ Tests that utils.get_secret reads the values of dev_secrets properly"""
    open(tmp_path / "test_secrets", "w", encoding="utf-8").write("A=B\nC=D")
    os.chdir(tmp_path)
    reload(utils)

    assert utils.get_secret("A") == "B"
    assert utils.get_secret("C") == "D"
    assert utils.get_secret("E") == ""


def test_root(tmp_path: pathlib.Path) -> None:
    """ Tests the Root Dir Logic"""
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
    """ Makes a Mock Message that has a predefined source and uuid"""
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

    async def get_cmd_output(self, text: str) -> str:
        """Runs commands as normal but intercepts the output instead of passing it onto signal"""
        await self.inbox.put(MockMessage(text))
        try:
            outgoing_msg = await asyncio.wait_for(self.outbox.get(), timeout=1)
            return outgoing_msg["params"]["message"]
        except asyncio.TimeoutError:
            return ""


@pytest.mark.asyncio
async def test_commands() -> None:
    """Tests commands"""
    bot = MockBot(BOT_NUMBER)
    
    # Enable Magic allows for mistyped commands
    os.environ["ENABLE_MAGIC"] = "1"
    
    # Tests do_ping with a mistyped command, expecting "/pong foo"
    assert await bot.get_cmd_output("/pingg foo") == "/pong foo"
    
    # slightly slow
    # assert "printer" in (await bot.get_output("/printerfactt")).lower()
    
    #tests the uptime command just checks to see if it starts with "Uptime: "
    assert (await bot.get_cmd_output("uptime")).startswith("Uptime: ")

    # tests that eval only works for admins
    assert (
        await bot.get_cmd_output("/eval 1+1")
    ) == "you must be an admin to use this command"

    assert (await bot.get_cmd_output("/help")).startswith("Documented commands:")
