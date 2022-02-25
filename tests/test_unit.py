import os
import pathlib

import pytest


def test_secrets(tmp_path: pathlib.Path) -> None:
    open(tmp_path / "dev_secrets", "w").write("A=B\nC=D")
    os.chdir(tmp_path)
    from forest import utils

    assert utils.get_secret("A") == "B"
    assert utils.get_secret("C") == "D"
    assert utils.get_secret("E") == ""
