#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
import functools
import logging
import os
import shutil
from pathlib import Path
from typing import Optional, cast

import phonenumbers as pn
from phonenumbers import NumberParseException


def FuckAiohttp(record: logging.LogRecord) -> bool:
    str_msg = str(getattr(record, "msg", ""))
    if "was destroyed but it is pending" in str_msg:
        return False
    if str_msg.startswith("task:") and str_msg.endswith(">"):
        return False
    return True


logger_class = logging.getLoggerClass()

logger = logging.getLogger()
logger.setLevel("DEBUG")
fmt = logging.Formatter("{levelname} {module}:{lineno}: {message}", style="{")
console_handler = logging.StreamHandler()
console_handler.setLevel(
    ((os.getenv("LOGLEVEL") or os.getenv("LOG_LEVEL")) or "DEBUG").upper()
)
console_handler.setFormatter(fmt)
console_handler.addFilter(FuckAiohttp)
logger.addHandler(console_handler)


#### Configure Parameters

# edge cases:
# accessing an unset secret loads other variables and potentially overwrites existing ones
def parse_secrets(secrets: str) -> dict[str, str]:
    pairs = [
        line.strip().split("=", 1)
        for line in secrets.split("\n")
        if line and not line.startswith("#")
    ]
    can_be_a_dict = cast(list[tuple[str, str]], pairs)
    return dict(can_be_a_dict)


# to dump: "\n".join(f"{k}={v}" for k, v in secrets.items())


@functools.cache  # don't load the same env more than once
def load_secrets(env: Optional[str] = None, overwrite: bool = False) -> None:
    if not env:
        env = os.environ.get("ENV", "dev")
    try:
        logging.info("loading secrets from %s_secrets", env)
        secrets = parse_secrets(open(f"{env}_secrets").read())
        if overwrite:
            new_env = secrets
        else:
            # mask loaded secrets with existing env
            new_env = secrets | os.environ
        os.environ.update(new_env)
    except FileNotFoundError:
        pass


# potentially split this into get_flag and get_secret; move all of the flags into fly.toml;
# maybe keep all the tomls and dockerfiles in a separate dir with a deploy script passing --config and --dockerfile explicitly
def get_secret(key: str, env: Optional[str] = None) -> str:
    try:
        secret = os.environ[key]
    except KeyError:
        load_secrets(env)
        secret = os.environ.get(key) or ""  # fixme
    if secret.lower() in ("0", "false", "no"):
        return ""
    return secret


## Parameters for easy access and ergonomic use

SIGNAL = (get_secret("SIGNAL") or "auxin").removesuffix("-cli") + "-cli"
AUXIN = SIGNAL.lower() == "auxin-cli"
APP_NAME = os.getenv("FLY_APP_NAME")
URL = os.getenv("URL_OVERRIDE", f"https://{APP_NAME}.fly.dev")
LOCAL = os.getenv("FLY_APP_NAME") is None
UPLOAD = DOWNLOAD = get_secret("DOWNLOAD")
ROOT_DIR = "/tmp/local-signal" if DOWNLOAD else "." if LOCAL else "/app"
MEMFS = get_secret("AUTOSAVE")

if Path(SIGNAL).exists():
    SIGNAL_PATH = str(Path(SIGNAL).absolute())
elif Path(ROOT_DIR) / SIGNAL:
    SIGNAL_PATH = str(Path(ROOT_DIR) / SIGNAL)
elif which := shutil.which(SIGNAL):
    SIGNAL_PATH = which
else:
    raise FileNotFoundError(
        f"Couldn't find a {SIGNAL} executable in the working directory, {ROOT_DIR}, or as an executable. "
        f"Install {SIGNAL} or try symlinking {SIGNAL} to the working directory"
    )


#### Configure logging to file

if get_secret("LOGFILES") or not LOCAL:
    handler = logging.FileHandler("debug.log")
    handler.setLevel("DEBUG")
    handler.setFormatter(fmt)
    handler.addFilter(FuckAiohttp)
    logger.addHandler(handler)


def signal_format(raw_number: str) -> Optional[str]:
    try:
        return pn.format_number(pn.parse(raw_number, "US"), pn.PhoneNumberFormat.E164)
    except NumberParseException:
        return None
