#!/usr/bin/python3.9
# Copyright (c) 2022 MobileCoin Inc.
# Copyright (c) 2022 The Forest Team
import datetime
import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional
import maya

from forest.core import QuestionBot, Message, Response, run_bot


class ScheduleBot(QuestionBot):
    """A bot that lets you schedule when to send a message"""

    # async def __init__(self, bot_number: Optional[str] = None) -> None:

    #     asyncio.create_task(self.midnight_job(self.report))
    #     super().__init__(bot_number)

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
            return self.get_outgoing_time(recipient)

        time_until = outgoing_time - maya.now()
        if time_until.total_seconds() < 0:
            await self.send_message(
                recipient, "You must select a time in the future. Please try again"
            )
            return self.get_outgoing_time(recipient)

        return outgoing_time

    async def do_schedule(self, message: Message) -> str:
        """
        Schedule a message to be sent at a specific later date. Format:
        Schedule "yyyy-mm-dd HH:MM TZ" "message"
        """

        # prompt = "I'm going to ask you some questions to determine how to best schedule your message. For starters, what message do you want to send?"
        # outgoing_message = self.ask_freeform_question(message.source, prompt)
        # if not (self.ask_yesno_question(message.source,f"You want me to send: \n {outgoing_message} is that correct? y/n")):
        #     return "oh ok, start over then"

        # prompt = "ok now let's figure out WHEN to send your message. Please provide a future time in the form of yyyy/mm/dd HH:MM Timezone"
        # outgoing_message = message.arg1

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

        asyncio.create_task(
            self.schedule_send_message(message.source, outgoing_message, outgoing_time)
        )
        return "Your message has been scheduled"

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
