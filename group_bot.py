import json
import logging
import sys
import asyncio
import utils
from forest.main import Bot, Message, Response


class GroupBot(Bot):
    last_group = None

    async def handle_message(self, message: Message) -> Response:
        if message.group:
            self.last_group = message.group
            cmd = {"command": "listGroups"}
            logging.info(cmd)
            await self.signalcli_input_queue.put(cmd)
        return await super().handle_message(message)

    async def handle_signalcli_raw_line(self, line: str) -> None:
        logging.info(line)
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            return await super().handle_signalcli_raw_line(line)
        if not (isinstance(blob, list) and self.last_group):
            return await super().handle_signalcli_raw_line(line)
        try:
            group_info = next(
                group for group in blob if group.get("id") == self.last_group
            )
            logging.info(blob)
        except StopIteration:
            return await super().handle_signalcli_raw_line(line)
        kick = [
            member
            for member in group_info.get("members", [])
            if member != self.bot_number
        ]
        if not kick:
            return await super().handle_signalcli_raw_line(line)
        cmd = {
            "command": "updateGroup",
            "remove-member": kick,
            "group": self.last_group,
        }
        self.last_group = None
        await self.signalcli_input_queue.put(cmd)
        for person in kick:
            await self.send_message(
                person,
                [
                    "here are the backsamples and associated costs",
                    "video: tit.mp4, cost: 0.5 MOB",
                ],
            )

async def start_session() -> None:
    try:
        number = utils.signal_format(sys.argv[1])
    except IndexError:
        number = utils.get_secret("BOT_NUMBER")
    new_session = GroupBot(number)
    asyncio.create_task(new_session.start_process())
    asyncio.create_task(new_session.handle_messages())
