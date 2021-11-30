#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

# pylint: disable=redefined-outer-name
import json
import logging
import pathlib
from collections import defaultdict
from subprocess import PIPE, Popen
from typing import Any, Iterator, Optional

import pytest

SIGNAL_CLI = "./signal-cli --config . -o json stdio".split()
DATA_DIR = pathlib.Path(".")
COUNTERPARTY = "+12406171615"
logging.basicConfig(
    format="{levelname} {module}:{lineno}: {message}",
    style="{",
)


class Reaction:
    def __init__(self, reaction: dict) -> None:
        assert reaction
        self.emoji = reaction["emoji"]
        self.author = reaction["targetAuthor"]
        self.ts = round(reaction["targetTimestamp"] / 1000)


class Message:
    """parses signal-cli output"""

    def __init__(self, envelope: dict) -> None:
        msg = envelope.get("dataMessage")
        if not msg:
            raise KeyError
        if not any(msg.get(k) for k in ("message", "reaction", "attachment")):
            raise KeyError
        self.sender: str = envelope["source"]
        self.sender_name = envelope.get("sourceName")
        self.ts = round(msg["timestamp"] / 1000)
        self.full_text = self.text = msg.get("message", "")
        try:
            self.reaction: Optional[Reaction] = Reaction(msg.get("reaction"))
        except (AssertionError, KeyError):
            self.reaction = None
        self.attachments = [
            str(DATA_DIR / attachment["id"])
            for attachment in msg.get("attachments", [])
        ]

    def __repr__(self) -> str:
        return f"<{self.sender_name}: {self.full_text}>"


class Signal:
    def __init__(self) -> None:
        # default number?
        self.received_messages: dict[int, dict[str, Message]] = defaultdict(dict)
        self.sent_messages: dict[int, dict[str, Message]] = defaultdict(dict)

    def __enter__(self) -> "Signal":
        self.proc = Popen(SIGNAL_CLI, stdin=PIPE, stdout=PIPE)
        logging.info("started signal-cli process")
        return self

    def __exit__(self, _: Any, value: Any, traceback: Any) -> None:
        self.proc.kill()
        logging.info("killed signal-cli process")

    def send(self, message: str, **kwargs: Any) -> None:
        command = {
            "command": "send",
            "recipient": [COUNTERPARTY],
            "message": message,
        }
        command.update(kwargs)
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(command).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def recv(self) -> Message:
        assert self.proc.stdout
        try:
            while 1:
                line = self.proc.stdout.readline().decode("utf-8")
                logging.info(line)
                try:
                    logging.info(line.strip())
                    msg = Message(json.loads(line)["envelope"])
                    return msg
                except (KeyError, json.JSONDecodeError, TypeError):
                    pass
        except KeyboardInterrupt:
            print("ignoring interrupt")
        raise Exception("couldn't receive messages")

    def communicate(self, message: str) -> Message:
        self.send(message)
        return self.recv()


@pytest.fixture
def signal() -> Iterator[Signal]:
    _signal = Signal()
    with _signal:
        yield _signal


def test_printerfact(signal: Signal) -> None:
    signal.send("TERMINATE", endsession=True)
    assert "printer" in signal.communicate("/printerfact").text.lower()


def send_sms(their_number: str, msg: str) -> None:
    # use forestbot staging?
    raise NotImplementedError


def test_groups(signal: Signal, our_number: str, their_number: str) -> None:
    signal.send("TERMINATE", endsession=True)
    # ensure number?
    group = signal.communicate(f"/mkgroup {their_number}")
    assert group  # idk
    reaction = signal.recv().reaction
    assert reaction
    assert reaction.emoji == "\N{Busts In Silhouette}"
    assert "Invited you to a group" in signal.recv().text
    send_sms(our_number, "foo")
    # requires sending to a group...
