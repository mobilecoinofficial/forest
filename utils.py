import os


def load_secrets(env = "dev") -> None:
    secrets = dict(line.strip().split("=", 1) for line in open(f"{env}_secrets"))
    os.environ.update(secrets)


def maybe_tunnel() -> tuple[Optional[asyncio.subprocess.Process], str]:
    if "FLY_APP_NAME" in os.environ:
        return  (None, os.environ["FLY_APP_NAME"] + ".fly.io"
    tunnel = await asyncio.subprocess.create_subprocess_exec(
        *("lt -p 8080".split()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    url = (await tunnel.stdout.readline()).decode().strip(
        "your url is: "
    ).strip()  + "/inbound"


