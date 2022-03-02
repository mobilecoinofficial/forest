import asyncio
import os
import pathlib
from importlib import reload
import pytest
from forest import utils
from forest.core import Message, QuestionBot


def test_secrets(tmp_path: pathlib.Path) -> None:
    open(tmp_path / "dev_secrets", "w").write("A=B\nC=D")
    os.chdir(tmp_path)
    reload(utils)

    assert utils.get_secret("A") == "B"
    assert utils.get_secret("C") == "D"
    assert utils.get_secret("E") == ""


def test_root(tmp_path: pathlib.Path) -> None:
    assert reload(utils).ROOT_DIR == "."
    os.chdir(tmp_path)
    open(tmp_path / "dev_secrets", "w").write("DOWNLOAD=1")
    assert reload(utils).ROOT_DIR == "/tmp/local-signal"
    os.environ["FLY_APP_NAME"] = "A"
    assert reload(utils).ROOT_DIR == "/app"


class MockMessage(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        self.source = "+" + "2" * 11
        super().__init__({})


class MockBot(QuestionBot):
    async def start_process(self) -> None:
        pass

    async def get_output(self, text: str) -> str:
        await self.inbox.put(MockMessage(text))
        try:
            msg = await asyncio.wait_for(self.outbox.get(), timeout=1)
            return msg["params"]["message"]
        except asyncio.TimeoutError:
            return ""


alice = "+" + "1" * 11


@pytest.mark.asyncio
async def test_commands() -> None:
    bot = MockBot(alice)
    assert await bot.get_output("/ping foo") == "/pong foo"
    assert "printer" in (await bot.get_output("printerfact")).lower()
    os.environ["ENABLE_MAGIC"] = True
    assert (await bot.get_output("/uptimee").startswith("Uptime: ")
