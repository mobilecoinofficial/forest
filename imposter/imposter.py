#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import logging
from typing import Union
from forest import utils
from forest.core import JSON, Message, Response, run_bot, rpc
from forest.memorybot import MemoryBot
from personate.core.reader_agent import ReaderAgent

# from acrossword import Ranker # For ranking facts & examples with plain Agent

# The bot class imports my new simple Agent class from Personate.
# It should set up a Frame, Ranker, Filter, knowledge and examples
# It should set Activators, (as synonyms of a do_activate function?)
# Can we set a Face here as well? (avatar, profile name etc)
# Can we build a Memory here? from pdictng?


class Imposter(MemoryBot):
    def __init__(self) -> None:
        # Accept a JSON config file in the same format as Personate.
        # Can be generated at https://ckoshka.github.io/personate/
        config_file = utils.get_secret("CONFIG_FILE")
        self.agent = ReaderAgent.from_json(config_file)
        # self.agent.ranker = Ranker() # Uncomment if using plain Agent with facts & examples
        super().__init__()

    def quotes_us(self, msg: Message) -> bool:
        if msg.quote:
            return msg.quote.author == self.bot_number or msg.quote.author
        return False

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
            or any(o.lower() in msg.full_text.lower() for o in self.agent.activators)
        ):
            return "generate_response"
        return super().match_command(msg)

    async def send_typing(self, msg: Message, stop=False):
        # Send a typing indicator in case the generator takes a while
        if msg.group:
            await self.outbox.put(
                rpc(
                    "sendTyping",
                    recipient=[msg.source],
                    group_id=[msg.group],
                    stop=stop,
                )
            )
        else:
            await self.outbox.put(rpc("sendTyping", recipient=[msg.source], stop=stop))

    async def get_templated_message(self, blob: Union[JSON, str]) -> str:
        content = self.get_message_content(blob)
        username = content["source"]
        if username == self.bot_number:
            username = self.agent.name
        if "quote" in content:
            quotes = await self.quote_chain(blob)
            quoted_msg = (
                "\n\n".join(
                    [
                        f"<{msg['quote']['author']}>: {msg['quote']['text']}"
                        for msg in quotes
                        if "quote" in msg
                    ]
                )
                + "\n\n"
            )
        else:
            quoted_msg = ""
        if "reactions" in content:
            reactions = f"\n{content['reactions']}"
        else:
            reactions = ""
        msg = f"{quoted_msg}<{username}>: {content['text']}{reactions}"
        return msg

    async def get_conversation(self, msg: Message, mem_length: int = 5) -> Response:
        user = self.get_user_id(msg)
        user_history = await self.get_user_history(user)
        if user_history:
            conversation = [
                await self.get_templated_message(blob)
                for blob in user_history[-mem_length:]
            ]
            return conversation
        return []

    async def do_context(self, msg: Message) -> Response:
        return await self.get_conversation(msg)

    async def do_hello(self, _: Message) -> str:
        return "Hello, world!"

    async def do_read_url(self, msg: Message) -> str:
        """
        Download and parse a URL, adding it to the knowledge base
        """
        await self.send_typing(msg)
        url = msg.arg1
        self.agent.add_knowledge(url, is_url=True)
        queue = self.agent.document_queue
        await self.agent.assemble_documents()
        await self.send_typing(msg, stop=True)
        return f"Acquired knowledge from {self.agent.document_collection.documents[-1].title}"

    async def do_generate_response(self, msg: Message) -> str:
        """
        Respond in character, using the Jurassic-1 API
        """
        # Get recent conversational context
        conversation = "\n\n".join(await self.get_conversation(msg, mem_length=3))
        # Or use just the last message for testing
        # conversation = msg.full_text

        # React with emoji and send typing indicator
        react_emoji = await self.agent.get_emoji(conversation)
        await self.send_reaction(msg, react_emoji)
        await self.send_typing(msg)

        # API call happens here, replace with below for rapid testing
        history = await self.get_user_history(self.get_user_id(msg))
        self.agent.add_facts(
            [
                self.get_message_content(blob)["text"]
                for blob in history
                if blob["source"] == self.bot_number
            ]
        )
        reply = await self.agent.generate_agent_response(conversation)
        reply = reply.strip()
        # reply = "TEST_REPLY"

        # Add an emoji at the end of message and stop typing indicator
        if len(reply) > 0:
            reply_emoji = await self.agent.get_emoji(reply)
            reply = f"{reply} {reply_emoji}"
        else:
            reply = "..."
        await self.send_typing(msg, stop=True)

        return reply


if __name__ == "__main__":
    run_bot(Imposter)
