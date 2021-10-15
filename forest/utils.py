import logging
import os
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


class TraceLogger(logger_class):  # type: ignore
    def trace(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self.log(TRACE, msg, *args, **kwargs)


logging.setLoggerClass(TraceLogger)

logging.basicConfig(
    level=os.getenv("LOGLEVEL", "DEBUG"),
    format="{levelname} {module}:{lineno}: {message}",
    style="{",
)
logger = logging.getLogger()
logging.getLogger().handlers[0].addFilter(FuckAiohttp)

# edge cases:
# accessing an unset secret loads other variables and potentially overwrites existing ones
# "false" being truthy is annoying


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


def get_secret(key: str, env: Optional[str] = None) -> str:
    try:
        return os.environ[key]
    except KeyError:
        load_secrets(env)
        return os.environ.get(key) or ""  # fixme


HOSTNAME = open("/etc/hostname").read().strip()  #  FLY_ALLOC_ID
APP_NAME = os.getenv("FLY_APP_NAME")
URL = f"https://{APP_NAME}.fly.dev"
LOCAL = APP_NAME is None
ROOT_DIR = (
    "." if get_secret("NO_DOWNLOAD") else "/tmp/local-signal" if LOCAL else "/app"
)

if get_secret("LOGFILES"):
    tracelog = logging.FileHandler("trace.log")
    tracelog.setLevel(TRACE)
    logger.addHandler(tracelog)
    handler = logging.FileHandler("debug.log")
    handler.setLevel("DEBUG")
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
