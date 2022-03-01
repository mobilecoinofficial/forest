import os
import pathlib
from importlib import reload
import pytest
from forest import utils


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
