import asyncio
from asyncore import loop
import logging
import os
import pathlib
from importlib import reload
from tracemalloc import stop
import pytest
import pytest_asyncio
from forest import utils, core
from forest.core import Message, QuestionBot, Response

# Sample bot number alice
BOT_NUMBER = "+11111111111"
USER_NUMBER = "+22222222222"

os.environ["ENV"] = "test"


class MockMessage(Message):
    """Makes a Mock Message that has a predefined source and uuid"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.full_text = text
        self.source = USER_NUMBER
        self.uuid = "cf3d7d34-2dcd-4fcd-b193-cbc6a666758b"
        self.mentions : list[dict[str,str]] = []
        super().__init__({})


class MockBot(QuestionBot):
    """Makes a bot that bypasses the normal start_process allowing
    us to have an inbox and outbox that doesn't depend on Signal"""

    async def start_process(self) -> None:
        pass

    async def send_input(self, text: str) -> None:
        """Puts a MockMessage in the inbox queue"""
        await self.inbox.put(MockMessage(text))

    async def get_output(self) -> str:
        """Reads messages in the outbox that would otherwise be sent over signal"""
        try:
            outgoing_msg = await asyncio.wait_for(self.outbox.get(), timeout=1)
            return outgoing_msg["params"]["message"]
        except asyncio.TimeoutError:
            logging.error("timed out waiting for output")
            return ""

    async def get_cmd_output(self, text: str) -> str:
        """Runs commands as normal but intercepts the output instead of passing it onto signal"""
        await self.send_input(text)
        return await self.get_output()

# class CallAndResponse():
#     def __init__(self,call:str,response:str) -> None:
#         call = 