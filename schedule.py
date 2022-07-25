#!/usr/bin/python3.9
# Copyright (c) 2022 MobileCoin Inc.
# Copyright (c) 2022 The Forest Team
import datetime
import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional
import maya

# from forest.pdictng import aPersistDict
from forest.core import QuestionBot, Message, Response, run_bot


class ScheduledMessage:
    """Scheduled Message Object"""

    def __init__(
        self,
        outgoing_message: str,
        outgoing_time: maya.MayaDT,
        outgoing_task: asyncio.Task,
    ) -> None:
        self.message = outgoing_message
        self.time = outgoing_time
        self.task = outgoing_task

    def time_until(self):
        """returns time until event in human readable format"""
        return self.time.slang_time()

    def __str__(self) -> str:
        return f"{self.message}"

    def timestamp(self):
        """returns timestamp for the event"""
        return self.time.datetime().timestamp()

    __repr__ = __str__


class ScheduleBot(QuestionBot):
    """A bot that lets you schedule when to send a message"""

    def __init__(self, bot_number: Optional[str] = None) -> None:
        self.scheduled_tasks: dict[str, list[ScheduledMessage]] = {}
        super().__init__(bot_number)

    # async def handle_message(self, message: Message) -> Response:

    #     # reset flow if user tries schedule again even if they're in the middle of a question
    #     if message.arg0 and message.arg0 == "schedule":
    #         return await self.do_schedule(message)

    #     return await super().handle_message(message)

    async def schedule_send_message(
        self, recipient, outgoing_message, outgoing_time
    ) -> None:
        """Function that schedules a message to send at a specific time"""
        ## TODO: it'd be neat if it could clean up after itself (delete task from self.scheduledmessages)
        #  but I can't figure out how to do it

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
        scheduled_message = ScheduledMessage(
            str(outgoing_message), outgoing_time, outgoing_task
        )

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
                f'{i + 1}) "{task.message}" \n{task.time_until()}'
                for i, task in enumerate(self.scheduled_tasks[message.source])
            ]
        )

    async def do_delete(self, message: Message) -> str:
        """delete a scheduled message"""

        options = [
            f"{x.message} <{x.timestamp()}>"
            for x in self.scheduled_tasks[message.source]
        ]
        choice = await self.ask_multiple_choice_question(
            message.source,
            "Which of these scheduled messages do you want to delete?",
            options,
        )
        for task in self.scheduled_tasks[message.source]:
            if f"{task.message} <{task.timestamp()}>" == choice:
                task.task.cancel()
                self.scheduled_tasks[message.source].remove(task)
                return f"{choice} deleted"

        return "there was a problem deleting your message"


if __name__ == "__main__":
    run_bot(ScheduleBot)
