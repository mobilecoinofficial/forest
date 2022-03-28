#!/usr/bin/python3.9
# Copyright (c) 2022 MobileCoin Inc.
# Copyright (c) 2022 The Forest Team
import datetime
import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional
import maya

from forest.core import QuestionBot, Message, run_bot



class ScheduleBot(QuestionBot):
    """A bot that lets you schedule when to send a message"""

    # async def __init__(self, bot_number: Optional[str] = None) -> None:
        
        
    #     asyncio.create_task(self.midnight_job(self.report))
    #     super().__init__(bot_number)

    

    async def schedule_send_message(self, recipient, outgoing_message, outgoing_time) -> None:
        """midnight job"""
        
        seconds_until_send = outgoing_time - maya.now()
        logging.info(
            "sleeping %s seconds until message sends", seconds_until_send
        )
        await asyncio.sleep(seconds_until_send.seconds)
        await self.send_message(recipient,outgoing_message)
    
    
    async def do_schedule(self, message: Message) -> str:
        """
        Schedule a message to be sent at a specific later date.
        """

        # prompt = "I'm going to ask you some questions to determine how to best schedule your message. For starters, what message do you want to send?"
        # outgoing_message = self.ask_freeform_question(message.source, prompt)
        # if not (self.ask_yesno_question(message.source,f"You want me to send: \n {outgoing_message} is that correct? y/n")):
        #     return "oh ok, start over then"
        
        # prompt = "ok now let's figure out WHEN to send your message. Please provide a future time in the form of yyyy/mm/dd HH:MM Timezone"
        # outgoing_message = message.arg1

        if not isinstance(message.arg1,str):
            time_info = await self.ask_freeform_question(message.source, "When do you want me to send your message?")
        else:
            time_info = message.text
        try:
            outgoing_time = maya.when(time_info)
        except ValueError:
            return "Couldn't understand that time. Try yyyy-mm-dd HH:MM TZ"

        outgoing_message = await self.ask_freeform_question(message.source,"What do you want your message to say?")

        time_until = outgoing_time-maya.now()

        asyncio.create_task(self.schedule_send_message(message.source,outgoing_message,outgoing_time))


        return f"Ok, we'll send \n\"{outgoing_message}\" \n for you in {time_until.seconds} seconds"
        
        # timestamp.datetime().strftime("%m/%d/%Y, %H:%M:%S %Z")






if __name__ == "__main__":
    run_bot(ScheduleBot)
