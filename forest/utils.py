import logging
import os
import functools
from asyncio.subprocess import PIPE, create_subprocess_exec
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, cast
import phonenumbers as pn
from phonenumbers import NumberParseException


def FuckAiohttp(record: logging.LogRecord) -> bool:
    str_msg = str(getattr(record, "msg", ""))
    if "was destroyed but it is pending" in str_msg:
        return False
    if str_msg.startswith("task:") and str_msg.endswith(">"):
        return False
    return True


TRACE = logging.DEBUG - 10
logging.addLevelName(TRACE, "TRACE")

logger_class = logging.getLoggerClass()

# doesn't work / not used
class TraceLogger(logger_class):  # type: ignore
    def trace(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(TRACE, msg, *args, **kwargs)


logging.setLoggerClass(TraceLogger)
logger = logging.getLogger()
logger.setLevel("DEBUG")
fmt = logging.Formatter("{levelname} {module}:{lineno}: {message}", style="{")
console_handler = logging.StreamHandler()
console_handler.setLevel(os.getenv("LOGLEVEL") or "INFO")
console_handler.setFormatter(fmt)
console_handler.addFilter(FuckAiohttp)
logger.addHandler(console_handler)

# edge cases:
# accessing an unset secret loads other variables and potentially overwrites existing ones
# "false" being truthy is annoying


@functools.cache  # don't load the same env more than once?
def load_secrets(env: Optional[str] = None, overwrite: bool = False) -> None:
    if not env:
        env = os.environ.get("ENV", "dev")
    try:
        logging.info("loading secrets from %s_secrets", env)
        secrets = [
            line.strip().split("=", 1)
            for line in open(f"{env}_secrets")
            if line and not line.startswith("#")
        ]
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


SIGNAL = get_secret("SIGNAL") or "auxin"
AUXIN = SIGNAL.lower() == "auxin"
HOSTNAME = open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID
APP_NAME = os.getenv("FLY_APP_NAME")
URL = f"https://{APP_NAME}.fly.dev"
LOCAL = APP_NAME is None
ROOT_DIR = (
    "." if get_secret("NO_DOWNLOAD") else "/tmp/local-signal" if LOCAL else "/app"
)
UPLOAD = DOWNLOAD = not get_secret("NO_DOWNLOAD")
MEMFS = not get_secret("NO_MEMFS")

if get_secret("LOGFILES") or not LOCAL:
    tracelog = logging.FileHandler("trace.log")
    tracelog.setLevel(TRACE)
    tracelog.setFormatter(fmt)
    logger.addHandler(tracelog)
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


@asynccontextmanager
async def get_url(port: int = 8080) -> AsyncIterator[str]:
    if not APP_NAME:
        try:
            logging.info("starting tunnel")
            tunnel = await create_subprocess_exec(
                *(f"lt -p {port}".split()),
                stdout=PIPE,
            )
            assert tunnel.stdout
            line = await tunnel.stdout.readline()
            url = line.decode().lstrip("your url is: ").strip()
            yield url + "/inbound"
        finally:
            logging.info("terminaitng tunnel")
            tunnel.terminate()
    else:
        yield APP_NAME + ".fly.io"
