from main import *


class GroupBot(Bot):
    last_group = None

    async def handle_message(self, message: Message) -> Response:
        if message.group:
            self.last_group = message.group
            cmd = {"command": "listGroups"}
            logging.info(cmd)
            await self.signalcli_input_queue.put(cmd)
        return await super().handle_message(message)

    async def handle_raw_signalcli_output(
        self, line: str, queue: Queue[Message]
    ) -> None:
        logging.info(line)
        try:
            blob = json.loads(line)
        except json.JSONDecodeError:
            return await super().handle_raw_signalcli_output(line, queue)
        if not (isinstance(blob, list) and self.last_group):
            return await super().handle_raw_signalcli_output(line, queue)
        try:
            group_info = next(
                group for group in blob if group.get("id") == self.last_group
            )
            logging.info(blob)
        except StopIteration:
            return await super().handle_raw_signalcli_output(line, queue)
        kick = [
            member
            for member in group_info.get("members", [])
            if member != self.bot_number
        ]
        if not kick:
            return await super().handle_raw_signalcli_output(line, queue)
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
