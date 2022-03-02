import os
import pathlib
from importlib import reload
import pytest
from forest import utils
from forest.core import Message, Bot


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


class MockBot(Bot):
    def __init__(self, number: str = "") -> None:
        pass


class MockMessage(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__({})


@pytest.mark.asyncio
async def test_ping() -> None:
    assert await MockBot().do_ping(MockMessage("/ping foo")) == "/pong foo"
