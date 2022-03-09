import asyncio
import os
import random
import shutil
import subprocess
from contextlib import redirect_stdout
from email.policy import default
from fileinput import filename
from functools import partial
from random import choice
from statistics import mode
from threading import Event
from time import sleep
from typing import Iterable, cast
from urllib.request import urlopen

import rich
from InquirerPy import base, inquirer, prompt, prompt_async, validator
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from rich.console import Console
from rich.markdown import Markdown
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt
from rich.text import Text

progress = Progress(
    TextColumn("[bold blue]{task.fields[filename]}", justify="right"),
    BarColumn(bar_width=None),
    "[progress.percentage]{task.percentage:>3.1f}%",
    "•",
    DownloadColumn(),
    "•",
    TransferSpeedColumn(),
    "•",
    TimeRemainingColumn(),
)

# to do:
# organize functions, dear god can i get some classes?
# prune dependencies, make things less verbose
# make stubs do something


# change this to opening from another file
tree = '''
                                                                @@@@.................................@@@
   ad88                                                         @.......................................
  d8"                                                ,d         ...........%%..............%%%..........
  88                                                 88         ...........%%..............%%%..........
MM88MMM ,adPPYba,  8b,dPPYba,  ,adPPYba, ,adPPYba, MM88MMM      ...........%%..............%%%..........
  88   a8"     "8a 88P'   "Y8 a8P_____88 I8[    ""   88         ....%%%%%%%%%%%%%%%.%%%%%%%%%%%%%%%%%...
  88   8b       d8 88         8PP"""""""  `"Y8ba,    88         ..........%%%%............%%%%%.........
  88   "8a,   ,a8" 88         "8b,   ,aa aa    ]8I   88,    .........%%%%%%%.........%%%%%%%........
  88    `"YbbdP"'  88          `"Ybbd8"' `"YbbdP"'   "Y888     ........%%.%%..%%%......%%*%%%%%%.......
                                ......#%%..%%....%/....%%..%%%.%%%......
                                .....%%%...%%........%%%...%%%..%%%.....
                                ...%%%,....%%......%%%#....%%%....%%%...
                                ....%......%%.....%%%......%%%.....%%...
                                ...........%%..............%%%..........
                                ...........%%..............%%%..........
                                @.......................................
'''
# prep opening the readme
rdme = open("README.md").read()

# make rich happy
style = "green"
console = Console()
tasks = [f"task {n}" for n in range(1, 11)]

# print art
console.print(tree, style=style)

# maybe i could reference something more portable to make changing menus around easier
def main():
    menu = inquirer.select(
        message="Welcome to the forest setup wizard.",
        choices=[
            Choice(value=settings, name="Get Started / Change Settings"),
            Choice(value=do_newbot, name="Start a new bot from a template"),
            Choice(value=do_docs, name="Read documentation"),
            Choice(value=do_update, name="Update"),
            Choice(value=do_deps, name="Install Dependencies"),
            Choice(value=do_exit, name="Exit"),
        ],
        default=None,
    ).execute()
    menu()


def settings():
    secrets = open("dev_secrets", "w+")  # this probably shouldn't be here
    current_secrets = secrets.read()
    pref = inquirer.select(
        message="What would you like to do?",
        choices=[
            Choice(value=do_number, name="Set bot number"),
            Choice(value=set_admin, name="Set admin number."),
            Choice(value=do_auxin, name="Switch to auxin"),
            Choice(value=do_rust, name="Set up Rust for Auxin", enabled=True),
            Choice(value=do_signalcli, name="Switch back to Signal-Cli."),
        ],
        default=None,
    ).execute()
    pref()


def do_docs():
    md = Markdown(readme)
    console.print(md)
    hint = Text()
    hint.append(
        "\nScroll up to read from the beginning!", style="bold green"
    )  # probably a better solution then this?
    console.print(hint)


def do_auxin():
    auxins = inquirer.select(
        message="Do you have auxin already?",
        choices=[
            Choice(
                value=build_auxin,
                name="No, build Auxin for me using cargo",
                enabled=True,
            ),
            Choice(
                value=switch_auxin,
                name="I have auxin, just change the parameter in 'dev_secrets'",
            ),
        ],
    ).execute()

    auxins()


def parse_secrets(secrets: str) -> dict[str, str]:
    pairs = [
        line.strip().split("=", 1)
        for line in secrets.split("\n")
        if line and not line.startswith("#")
    ]
    can_be_a_dict = cast(list[tuple[str, str]], pairs)
    return dict(can_be_a_dict)


def change_secrets(new_values: dict[str, str], **kwargs: str) -> None:
    env = os.environ.get("ENV", "dev")
    secrets = parse_secrets(open(f"{env}_secrets").read())
    # py3.9 introduced dict unions
    changed = secrets | new_values | kwargs
    open(f"{env}_secrets", "w").write("\n".join(f"{k}={v}" for k, v in changed))


def do_number():
    NUMBER = Prompt.ask(
        "Please enter your bot's phone number in international format, e.x: +19991238458"
    )
    change_secrets({"BOT_NUMBER": NUMBER})


def set_admin() -> None:
    change_secrets({"ADMIN": Prompt.ask("What's your number?")})


def do_rust():
    with console.status("[bold green]Setting up rust 'sh rust.sh'...") as status:
        while tasks:
            task = tasks.pop(0)
            get_rust()
            os.system("sh rust.sh")


def get_rust():
    return subprocess.run(
        "curl -o rust.sh --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs",
        shell=True,
    )


def build_auxin():
    os.system("git clone https://github.com/mobilecoinofficial/auxin.git")
    os.system("rustup default nightly")


def switch_auxin():
    return "switching auxin stuff here"


def do_update():
    with console.status("[bold green]git pull") as status:
        while tasks:
            return os.system("git pull")


def do_signalcli():
    task1 = progress.add_task("Downloading...")
    v = "0.10.3"
    copy_url(
        task1,
        url=f"https://github.com/AsamK/signal-cli/releases/download/v{v}/signal-cli-{v}-Linux.tar.gz",
        path="./signal-cli.tar.gz",
    )
    with console.status("[bold green]unzipping..") as status:
        task2 = progress.add_task(
            "unzip",
        )
        do_unzip_signal(archive="signal-cli.tar.gz")


# change this to something generic
def do_unzip_signal(archive):
    archive = "signal-cli.tar.gz"
    os.system("tar -xvf {}".format(archive))


def do_newbot():
    newbot = inquirer.select(
        message="What template would you like to start with?",
        choices=[
            Choice(value=do_hellobot, name="HelloBot"),
        ],
    ).execute()
    print(newbot())


# make this generic
def do_hellobot():
    shutil.copyfile("./sample_bots/hellobot.py", "bot.py")
    return "Okay, your brand new bot template is in your Forest directory!"


# why
def do_deps():
    os.system("sh setup.sh")


def do_exit():
    exit()


# what needs this?
done_event = Event()


def handle_sigint(signum, frame):
    done_event.set()


# maybe put these in a class
def copy_url(task_id: 1, url: str, path: str) -> None:
    progress.console.log(f"Requesting {url}")
    response = urlopen(url)
    # This will break if the response doesn't contain content length
    progress.update(task_id, total=int(response.info()["Content-length"]))
    with open(path, "wb") as dest_file:
        progress.start_task(task_id)
        for data in iter(partial(response.read, 32768), b""):
            dest_file.write(data)
            progress.update(task_id, advance=len(data))
            if done_event.is_set():
                return
    progress.console.log(f"Downloaded {path}")


def download(urls: Iterable[str], dest_dir: str):
    with progress:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for url in urls:
                filename = url.split("/")[-1]
                dest_path = os.path.join(dest_dir, filename)
                task_id = progress.add_task("download", filename=filename, start=False)
                pool.submit(copy_url, task_id, url, dest_path)


if __name__ == "__main__":
    main()
