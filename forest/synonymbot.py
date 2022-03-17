#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

from typing import Tuple, Protocol, Any
from forest.core import Bot, Message, Response, requires_admin, is_admin, run_bot
from forest.pdictng import aPersistDictOfLists


class SynonymAttributes(Protocol):
    syns: list


def has_synonyms(func: Any) -> SynonymAttributes:
    return func


class SynonymBot(Bot):
    def __init__(self) -> None:
        self.synonyms: aPersistDictOfLists[str] = aPersistDictOfLists("synonyms")
        super().__init__()

    def get_valid_syns(self, msg: Message) -> Tuple:
        "Get commands and synonyms without leaking admin commands"
        valid_cmds = self.commands if is_admin(msg) else self.visible_commands
        valid_syns = {k: v for k, v in self.synonyms.dict_.items() if k in valid_cmds}
        return (valid_cmds, valid_syns)

    @requires_admin
    async def do_build_synonyms(self, _: Message) -> str:
        """Build synonyms from in-code definitions.

        Run this command as admin when bot is first deployed.
        """
        for cmd in self.commands:
            command = "do_" + cmd
            method = None
            # check for the command
            if hasattr(self, command):
                method = getattr(self, command)
            if method is not None:
                if hasattr(method, "syns"):
                    syns = getattr(method, "syns")
                    await self.synonyms.set(cmd, syns)
        return f"Built synonym list: {self.synonyms}"

    @requires_admin
    async def do_clear_synonyms(self, _: Message) -> str:
        "Remove all synonyms from persistent storage. Admin-only"
        cmds = await self.synonyms.keys()
        for cmd in cmds:
            await self.synonyms.remove(cmd)
        return "Synonym list cleared"

    async def do_list_synonyms(self, msg: Message) -> str:
        "Print synonyms for all commands, or a single command if included"
        valid_cmds, valid_syns = self.get_valid_syns(msg)
        if msg.arg1 in valid_cmds:
            syns = await self.synonyms.get(str(msg.arg1))
            return f"Synonyms for '{msg.arg1}' are: {syns}"
        if any(msg.arg1 in v for v in valid_syns.values()):
            cmds = [k for k, v in valid_syns.items() if msg.arg1 in v]
            return f"'{msg.arg1}' is a synonym for {cmds}"
        return f"Synonym list: {valid_syns}"

    async def do_link(self, msg: Message) -> str:
        "Link a command to a synonym"
        valid_cmds, valid_syns = self.get_valid_syns(msg)
        if msg.arg1 in valid_cmds:
            if msg.arg2:
                # Check if the synonym already in use
                if msg.arg2 in valid_cmds:
                    return f"Sorry, '{msg.arg2}' is a command"
                if any(msg.arg2 in v for v in valid_syns.values()):
                    cmds = [k for k, v in valid_syns.items() if msg.arg2 in v]
                    return f"Sorry, '{msg.arg2}' is already associated with one or more commands: {cmds}"
                # Happy path, add the synonym
                if msg.arg1 not in valid_syns.keys():
                    await self.synonyms.set(str(msg.arg1), [msg.arg2])
                else:
                    await self.synonyms.extend(str(msg.arg1), msg.arg2)
                return f"Linked synonym '{msg.arg2}' to command '{msg.arg1}'"
            # No synonym detected
            return f"Need a synonym to link to command '{msg.arg1}', try again"
        # No command detected
        return "Not a valid command. Syntax for linking commands is 'link command synonym'. Please try again"

    async def do_unlink(self, msg: Message) -> str:
        "Remove a command from a synonym"
        valid_cmds, valid_syns = self.get_valid_syns(msg)
        # Look for a command
        if msg.arg1 in valid_cmds:
            syns = valid_syns[msg.arg1]
            # Happy path, remove the synonym
            if msg.arg2 and msg.arg2 in syns:
                await self.synonyms.remove_from(str(msg.arg1), str(msg.arg2))
                return f"Unlinked synonym '{msg.arg2}' from command '{msg.arg1}'"
            # No synonym detected
            return f"Need a synonym to unlink from command '{msg.arg1}'. Valid synonyms are {syns}"
        # Look for a synonym by itself
        if any(msg.arg1 in v for v in valid_syns.values()):
            cmds = [k for k, v in valid_syns.items() if msg.arg1 in v]
            print(cmds)
            # Synonym points to multiple commands
            if len(cmds) > 1:
                return f"Multiple commands have that synonym: {cmds}. Please try again in the form 'unlink command synonym'"
            # Only points to one command, remove the synonym
            if len(cmds) == 1:
                await self.synonyms.remove_from(cmds[0], str(msg.arg1))
                return f"Synonym '{msg.arg1}' removed from command '{cmds[0]}'"
        return "Syntax for unlinking commands is 'unlink command synonym', try again"

    def match_command(self, msg: Message) -> str:
        if not msg.arg0:
            return ""
        # Look for direct match before checking synonyms
        if hasattr(self, "do_" + msg.arg0):
            return msg.arg0
        # Try synonyms
        _, valid_syns = self.get_valid_syns(msg)
        for k, v in valid_syns.items():
            if msg.arg0 in v:
                return k
        # Pass the buck
        return super().match_command(msg)

    # We can add synonyms in development. give your command the
    # @has_synonyms decorator and register a .syns attribute
    @has_synonyms
    async def do_hello(self, _: Message) -> str:
        return "Hello, world!"

    do_hello.syns = ["hi", "hey", "whatup", "aloha"]

    @has_synonyms
    async def do_goodbye(self, _: Message) -> str:
        return "Goodbye, cruel world!"

    do_goodbye.syns = ["bye", "goodby", "later", "aloha"]

    # We can also add.syns attributes to inherited methods in this manner
    # but doesn't play well with mypy, becuase the signature doesn't match
    # the supertype, so we use type:ignore, as seen below        ↓
    async def do_help(self, msg: Message) -> Response:
        return await super().do_help(msg)

    do_help.syns = ["documentation", "docs", "commands", "man"]  # type:ignore


if __name__ == "__main__":
    run_bot(SynonymBot)
