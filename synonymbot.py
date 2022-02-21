#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import asyncio
from forest.core import Bot, Message, requires_admin, is_admin, run_bot
from forest.pdictng import aPersistDict


class SynonymBot(Bot):

    def __init__(self) -> None:
        self.synonyms = aPersistDict("synonyms")
        super().__init__()

    @requires_admin
    async def do_build_synonyms(self, _) -> str:
        for short_cmd in self.commands:
            command = "do_" + short_cmd
            method = None
            if hasattr(self, command):
                method = getattr(self, command)
            if hasattr(super, command):
                method = getattr(self, command)
            if method is not None:
                if hasattr(method, "syns"):
                    syns = getattr(method, "syns")
                    await self.synonyms.set(short_cmd, syns)
        if syns := self.synonyms:
            return(f'Built synonym list: {syns}')
    
    @requires_admin
    async def do_clear_synonyms(self, _) -> str:
        cmds = await self.synonyms.keys()
        for cmd in cmds:
            await self.synonyms.remove(cmd)
        return('Synonym list cleared')

    async def do_list_synonyms(self, msg: Message) -> str:
        valid_commands = self.commands if is_admin(msg) else self.visible_commands
        if msg.arg1 in valid_commands:
            syns = await self.synonyms.get(msg.arg1)
            return f"Synonyms for {msg.arg1} are: {syns}"
        else:
            syns = self.synonyms.dict_
            valid_syns = {k:v for k, v in syns.items() if k in valid_commands}
            return f"Synonym list: {valid_syns}"

    async def do_link(self, msg: Message) -> str:
        valid_commands = self.commands if is_admin(msg) else self.visible_commands
        if msg.arg1 in valid_commands:
            if msg.arg2:
                syns = await self.synonyms.get(msg.arg1)
                if syns is None:
                    await self.synonyms.set(msg.arg1, [msg.arg2])
                else:
                    await self.synonyms.extend(msg.arg1, msg.arg2)
                return f"Linked synonym {msg.arg2} to command {msg.arg1}"
            else:
                return f"Need a synonym to link to command '{msg.arg1}', try again"
        return "Syntax for linking commands is 'link command synonym', try again"

    async def do_unlink(self, msg: Message) -> str:
        valid_commands = self.commands if is_admin(msg) else self.visible_commands
        if msg.arg1 in await self.synonyms.keys():
            syns = await self.synonyms.get(msg.arg1)
            if msg.arg2 in syns:
                await self.synonyms.remove_from(msg.arg1, msg.arg2)
                return f"Unlinked synonym {msg.arg2} from command {msg.arg1}"
            else:
                return f"Need a synonym to unlink from command '{msg.arg1}'. Valid synonyms are {syns}"
        return "Syntax for unlinking commands is 'unlink command synonym', try again"

    async def do_hello(self, _: Message) -> str:
        return "Hello, world!"
    do_hello.syns = ['hi', 'hey', 'whatup']

    async def do_goodbye(self, _: Message) -> str:
        return "Goodbye, cruel world!"
    do_goodbye.syns = ['bye', 'goodby', 'later' ]

if __name__ == "__main__":
    run_bot(SynonymBot)
