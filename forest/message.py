"""
FYI: this module uses a lot of `or`. `None or 1` is `1`, not `True`.
We're using this because you can have `{"attachments": null}` in JSON, which
breaks our typing if we expect Message.attachments to be list[str].
Using `or` like this is a bit of a hack, but it's what we've got.
"""
import shlex
from typing import Optional

from forest.utils import logging


class Message:
    """
    Base message type

    Attributes
    -----------
    blob: dict
       blob representing the jsonrpc message
    """

    timestamp: int
    text: str
    attachments: list[dict[str, str]]
    group: Optional[str]
    quoted_text: str
    source: str
    payment: dict
    reactions: dict[str, str]

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        # parsing
        self.command: Optional[str] = None
        self.tokens: Optional[list[str]] = None
        if self.text and self.text.startswith("/"):
            command, *self.tokens = shlex.split(self.text)
            self.command = command[1:]  # remove /
            self.arg1 = self.tokens[0] if self.tokens else None
            self.text = " ".join(self.tokens)
        elif self.text and "help" in self.text.lower():
            self.command = "help"

    def to_dict(self) -> dict:
        """
        Returns a dictionary of message instance
        variables except for the blob
        """
        properties = {}
        for attr in dir(self):
            if not (attr.startswith("_") or attr in ("blob", "full_text", "envelope")):
                val = getattr(self, attr)
                if val and not callable(val):
                    # if attr == "text":
                    #    val = termcolor.colored(val, attrs=["bold"])
                    #    # gets mangled by repr
                    properties[attr] = val

        return properties

    def __getattr__(self, attr: str) -> None:
        # return falsy string back if not found
        return None

    def __repr__(self) -> str:
        return f"Message: {self.to_dict()}"


class AuxinMessage(Message):
    def __init__(self, outer_blob: dict, _id: Optional[str] = None) -> None:
        if "id" in outer_blob:
            self.id = outer_blob["id"]
            self.error = outer_blob.get("error", {})
            blob = outer_blob.get("result", {})
            if not isinstance(blob, dict):
                blob = {}
        else:
            self.id = _id
            blob = outer_blob
        # logging.info("msg id: %s", self.id)
        self.timestamp = blob.get("timestamp", -1)
        content = blob.get("content", {})
        msg = (content.get("source") or {}).get("dataMessage") or {}
        self.text = self.full_text = msg.get("body") or ""
        self.attachments: list[dict[str, str]] = msg.get("attachments", [])
        self.group = msg.get("group") or msg.get("groupV2") or ""
        maybe_quote = msg.get("quote")
        self.address = blob.get("Address", {})
        self.quoted_text = "" if not maybe_quote else maybe_quote.get("text")
        address = blob.get("remote_address", {}).get("address", {})
        if "Both" in address:
            self.source, self.uuid = address["Both"]
        elif "Uuid" in address:
            self.uuid = address.get("Uuid")
            if self.text:
                logging.error("text message has no number: %s", outer_blob)
        elif "Phone" in address:
            self.source = address["Phone"]
        else:
            if self.text:
                logging.error("text message has no remote address: %s", outer_blob)
        if self.text and not self.source:
            logging.error(outer_blob)
        payment_notif = (
            (msg.get("payment") or {}).get("Item", {}).get("notification", {})
        )
        if payment_notif:
            receipt = payment_notif["Transaction"]["mobileCoin"]["receipt"]
            self.payment = {
                "note": payment_notif.get("note"),
                "receipt": receipt,
            }
        else:
            self.payment = {}
        if self.text:
            logging.info(self)  # "parsed a message with body: '%s'", self.text)
        super().__init__(blob)


class Reaction:
    def __init__(self, reaction: dict) -> None:
        assert reaction
        self.emoji = reaction["emoji"]
        self.author = reaction["targetAuthor"]
        self.ts = round(reaction["targetTimestamp"] / 1000)


class StdioMessage(Message):
    """Represents a Message received from signal-cli, optionally containing a command with arguments."""

    def __init__(self, blob: dict) -> None:
        self.id = blob.get("id")
        result = blob.get("result", {})
        self.envelope = envelope = blob.get("envelope", {})
        # {"envelope":{"source":"+***REMOVED***","sourceNumber":"+***REMOVED***","sourceUuid":"412e180d-c500-4c60-b370-14f6693d8ea7","sourceName":"sylv","sourceDevice":3,"timestamp":1637290589910,"dataMessage":{"timestamp":1637290589910,"message":"/ping","expiresInSeconds":0,"viewOnce":false}},"account":"+447927948360"}
        self.source: str = envelope.get("source")
        self.name: str = envelope.get("sourceName") or self.source
        self.timestamp = envelope.get("timestamp") or result.get("timestamp")

        # msg data
        msg = envelope.get("dataMessage", {})
        # "attachments":[{"contentType":"image/png","filename":"image.png","id":"1484072582431702699","size":2496}]}
        self.attachments: list[dict[str, str]] = msg.get("attachments")
        self.full_text = self.text = msg.get("message", "")
        self.group: Optional[str] = msg.get("groupInfo", {}).get(
            "groupId"
        ) or result.get("groupId")
        self.quoted_text = msg.get("quote", {}).get("text")
        self.payment = msg.get("payment")
        try:
            self.reaction: Optional[Reaction] = Reaction(msg.get("reaction"))
        except (AssertionError, KeyError):
            self.reaction = None
        self.reactions: dict[str, str] = {}
        if self.text:
            logging.info(self)  # "parsed a message with body: '%s'", self.text)
        super().__init__(blob)
