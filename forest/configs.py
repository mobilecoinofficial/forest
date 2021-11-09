import logging
import os
import functools
from asyncio.subprocess import PIPE, create_subprocess_exec
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, cast

#### Configure Logging

def MuteAiohttpNoise(record: logging.LogRecord) -> bool:
    str_msg = str(getattr(record, "msg", ""))
    if "was destroyed but it is pending" in str_msg:
        return False
    if str_msg.startswith("task:") and str_msg.endswith(">"):
        return False
    return True

logger = logging.getLogger()
logger.setLevel("DEBUG")
fmt = logging.Formatter("{levelname} {module}:{lineno}: {message}", style="{")
console_handler = logging.StreamHandler()
console_handler.setLevel(os.getenv("LOGLEVEL") or "INFO")
console_handler.setFormatter(fmt)
console_handler.addFilter(MuteAiohttpNoise)
logger.addHandler(console_handler)

#### Configure Parameters

# edge cases:
# accessing an unset secret loads other variables and potentially overwrites existing ones
# "false" being truthy is annoying


@functools.cache  # don't load the same env more than once?
def load_secrets(env: Optional[str] = None, overwrite: bool = False) -> None:
    if not env:
        env = os.environ.get("ENV", "dev")
    try:
        logging.info("loading secrets from %s_secrets", env)
        secrets = [line.strip().split("=", 1) for line in open(f"{env}_secrets")]
        can_be_a_dict = cast(list[tuple[str, str]], secrets)
        if overwrite:
            new_env = dict(can_be_a_dict)
        else:
            new_env = (
                dict(can_be_a_dict) | os.environ
            )  # mask loaded secrets with existing env
        os.environ.update(new_env)
    except FileNotFoundError:
        pass


# TODO: split this into get_flag and get_secret; move all of the flags into fly.toml;
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


#### Parameters for easy access and ergonomic use.

AUXIN = get_secret("AUXIN") or True
HOSTNAME = open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID
APP_NAME = os.getenv("FLY_APP_NAME")
URL = f"https://{APP_NAME}.fly.dev"
LOCAL = APP_NAME is None
ROOT_DIR = (
    "." if get_secret("NO_DOWNLOAD") else "/tmp/local-signal" if LOCAL else "/app"
)

UPLOAD = DOWNLOAD = not get_secret("NO_DOWNLOAD")
MEMFS = not get_secret("NO_MEMFS")

#### Configure Logging to File

if get_secret("LOGFILES") or not LOCAL:
    handler = logging.FileHandler("debug.log")
    handler.setLevel("DEBUG")
    handler.setFormatter(fmt)
    handler.addFilter(MuteAiohttpNoise)
    logger.addHandler(handler)
