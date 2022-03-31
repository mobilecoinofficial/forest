#!/usr/bin/python3.9
# Copyright (c) 2022 MobileCoin Inc.
# Copyright (c) 2022 The Forest Team
import datetime
import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional
import maya
from forest.pdictng import aPersistDict
from forest.core import QuestionBot, Message, Response, run_bot

class ScheduledMessage():
    def __init__(self, outgoing_message, outgoing_time, outgoing_task) -> None:
        self.message = outgoing_message
        self.time = outgoing_time
        self.task = outgoing_task

    def time_until(self):
        return self.time.slang_time()



class ScheduleBot(QuestionBot):
    """A bot that lets you schedule when to send a message"""

    def __init__(self, bot_number: Optional[str] = None) -> None:
        self.scheduled_tasks = {}
        super().__init__(bot_number)

    async def handle_message(self, message: Message) -> Response:

        # reset flow if user tries schedule again even if they're in the middle of a question
        if message.arg0 and message.arg0 == "schedule":
            return await self.do_schedule(message)

        return await super().handle_message(message)

    async def schedule_send_message(
        self, recipient, outgoing_message, outgoing_time
    ) -> None:
        """Function that schedules a message to send at a specific time"""

        seconds_until_send = outgoing_time - maya.now()
        logging.info("sleeping %s seconds until message sends", seconds_until_send)
        await asyncio.sleep(seconds_until_send.seconds)
        await self.send_message(recipient, outgoing_message)


    def parse_schedule(self, message: Message) -> tuple[maya.MayaDT, Optional[str]]:
        """Parse a message of the form schedule "yyyy-mm-dd HH:MM" "message" """

        try:
            outgoing_time = maya.when(message.arg1)
        except ValueError:
            try:
                logging.info("couldn't get time from arg1, trying from whole message")
                outgoing_time = maya.when(message.text)
            except ValueError:
                logging.info(
                    "couldn't get time from message. Failed to extract time from message"
                )
                outgoing_time = None

        outgoing_message = message.arg2

        return outgoing_time, outgoing_message

    async def get_outgoing_time(self, recipient) -> maya.MayaDT:
        """Hammer down an exact time an user wants their message sent"""
        input_time = await self.ask_freeform_question(
            recipient,
            'Give me a time, preferrably in "yyyy-mm-dd HH:MM TimeZone" format.',
        )
        if input_time in self.TERMINAL_ANSWERS:
            return None
        try:
            outgoing_time = maya.when(input_time)
        except ValueError:
            await self.send_message(
                recipient,
                "Sorry. I couldn't parse that as a time. Let's try again, or say \"cancel\" to cancel.",
            )
            return await self.get_outgoing_time(recipient)

        time_until = outgoing_time - maya.now()
        if time_until.total_seconds() < 0:
            await self.send_message(
                recipient, "You must select a time in the future. Please try again"
            )
            return await self.get_outgoing_time(recipient)

        return outgoing_time

    async def do_schedule(self, message: Message) -> str:
        """
        Schedule a message to be sent at a specific later date. Format:
        Schedule "yyyy-mm-dd HH:MM TZ" "message"
        """

        outgoing_time, outgoing_message = self.parse_schedule(message)
        cancel_message = "Ok, cancelling. Please try again"

        if not outgoing_time:
            confirmation = await self.ask_yesno_question(
                message.source,
                f'You want to schedule\n"{outgoing_message}"\nTo send as a message? (y/n)',
            )
            if confirmation is None:
                return cancel_message

            if not confirmation:
                outgoing_message = await self.ask_freeform_question(
                    message.source, "Ok, tell me what you want to send as a message."
                )
                if outgoing_message.lower() in self.TERMINAL_ANSWERS:
                    return cancel_message

            outgoing_time = await self.get_outgoing_time(message.source)

            if outgoing_time is None:
                return cancel_message

        time_until = outgoing_time - maya.now()
        if time_until.total_seconds() < 0:
            await self.send_message(
                message.source,
                "You must select a time in the future. Please try again.",
            )
            outgoing_time = await self.get_outgoing_time(message)
            if outgoing_time is None:
                return cancel_message

        confirmation = await self.ask_yesno_question(
            message.source,
            f'Ok, shall I schedule:\n"{outgoing_message}"\nTo send at {str(outgoing_time)} which is {outgoing_time.slang_time()}(y/n)',
        )
        if not confirmation:
            return cancel_message

        outgoing_task = asyncio.create_task(
            self.schedule_send_message(message.source, outgoing_message, outgoing_time)
        )
        scheduled_message = ScheduledMessage(outgoing_message, outgoing_time, outgoing_task)
        if message.source not in self.scheduled_tasks:
            self.scheduled_tasks[message.source] = []
        self.scheduled_tasks[message.source].append(scheduled_message)
        return "Your message has been scheduled"

    async def do_get_scheduled_messages(self, message: Message) -> str:
        """
        Get a list of all scheduled messages for a user
        """
        if not self.scheduled_tasks[message.source]:
            return "You have no scheduled messages."

        return "Your scheduled messages:\n" + "\n".join(
            [
                f'{i + 1}. {task.time_until()}'
                for i, task in enumerate(self.scheduled_tasks[message.source])
            ]
        )





        # if not isinstance(message.arg1, str):
        #     time_info = await self.ask_freeform_question(
        #         message.source, "When do you want me to send your message?"
        #     )
        # else:
        #     time_info = message.text
        # try:
        #     outgoing_time = maya.when(time_info)
        # except ValueError:
        #     return "Couldn't understand that time. Try yyyy-mm-dd HH:MM TZ"

        # outgoing_message = await self.ask_freeform_question(
        #     message.source, "What do you want your message to say?"
        # )

        # time_until = outgoing_time - maya.now()

        # asyncio.create_task(
        #     self.schedule_send_message(message.source, outgoing_message, outgoing_time)
        # )

        # return f'Ok, we\'ll send \n"{outgoing_message}" \n for you in {time_until.seconds} seconds at {outgoing_time.datetime()}'
        # # timestamp.datetime().strftime("%m/%d/%Y, %H:%M:%S %Z")


if __name__ == "__main__":
    run_bot(ScheduleBot)
