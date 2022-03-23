#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

from random import randint
from re import template
from forest import utils
from forest.core import Bot, Message, Response, run_bot, rpc
from personate.core.reader_agent import ReaderAgent

from acrossword import Ranker

# The bot class imports my new simple Agent class from Personate.
# It should set up a Frame, Ranker, Filter, knowledge and examples
# It should set Activators, (as synonyms of a do_activate function?)
# Can we set a Face here as well? (avatar, profile name etc)
# Can we build a Memory here? from pdictng?


class Imposter(Bot):
    def __init__(self) -> None:
        # Accept a JSON config file in the same format as Personate.
        # Can be generated at https://ckoshka.github.io/personate/
        config_file = utils.get_secret("CONFIG_FILE")
        self.agent = ReaderAgent.from_json(config_file)
        super().__init__()

    def quotes_us(self, msg: Message) -> bool:
        if msg.quote:
            return msg.quote.author == self.bot_number or msg.quote.author

    def match_command(self, msg: Message) -> str:
        if not msg.arg0:
            return ""
        # Look for direct match before checking activators
        if hasattr(self, "do_" + msg.arg0):
            return msg.arg0
        # If we're not in a group we can just respond to the message,
        # Otherwise try activators
        if (
            (msg.full_text and not msg.group)
            or self.mentions_us(msg)
            or self.quotes_us(msg)
            or any(o in msg.full_text for o in self.agent.activators)
        ):
            return "generate_response"
        # Pass the buck
        return super().match_command(msg)

    async def send_typing(self, msg: Message, stop=False):
        # Send a typing indicator in case the generator takes a while
        if msg.group:
            await self.outbox.put(rpc("sendTyping", groupId=[msg.group], stop=stop))
        else:
            await self.outbox.put(rpc("sendTyping", recipient=[msg.source], stop=stop))

    async def do_hello(self, _: Message) -> str:
        return "Hello, world!"

    async def do_read_url(self, msg: Message) -> str:
        await self.send_typing(msg)
        url = msg.arg1
        self.agent.add_knowledge(url, is_url=True)
        queue = self.agent.document_queue
        await self.agent.assemble_documents()
        await self.send_typing(msg, stop=True)
        return f"Acquired knowledge from {self.agent.document_collection.documents[-1].title}"

    async def do_generate_response(self, msg: Message) -> str:
        react_emoji = await self.agent.get_emoji(msg.full_text)
        await self.send_reaction(msg, react_emoji)
        await self.send_typing(msg)
        # API call happens here, replace with below for rapid testing
        reply = await self.agent.generate_agent_response(msg.full_text)
        # reply = "TEST_REPLY"
        reply_emoji = await self.agent.get_emoji(reply)
        reply = f"{reply} {reply_emoji}"
        await self.send_typing(msg, stop=True)
        return reply


if __name__ == "__main__":
    run_bot(Imposter)
