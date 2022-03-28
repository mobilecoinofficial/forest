import logging
from typing import Optional, Any
from forest.core import Bot, Message, Response, run_bot
from forest.pdictng import aPersistDictOfLists
from forest.message import Reaction


class MemoryBot(Bot):
    def __init__(self, bot_number: Optional[str] = None) -> None:
        super().__init__(bot_number)
        self.msgs: aPersistDictOfLists[dict] = aPersistDictOfLists("msgs")

    async def handle_message(self, message: Message) -> Response:
        if message.reaction:
            logging.info("saw a reaction")
            return await self.handle_reaction(message)
        user = message.source
        if message.group:
            user = str(message.group)
        if message.full_text:
            blob = message.to_dict()
            blob["reactions"] = []
            await self.msgs.extend(user, blob)
        return await super().handle_message(message)

    def get_user_id(self, msg: Message) -> Response:
        if type(msg) is dict:
            if "source" in msg:
                if "group-id" in msg:
                    user = msg["group-id"]
                user = msg["source"]
        else:
            user = msg.source
            if msg.group:
                user = str(msg.group)
        return user

    async def get_user_history(self, msg: Message) -> Response:
        user = self.get_user_id(msg)
        user_history = await self.msgs.get(user)
        if user_history:
            return [blob for blob in user_history]
        return None

    async def get_user_message(self, msg: Message, timestamp: int) -> Response:
        user_history = await self.get_user_history(msg)
        if user_history:
            blob = next((o for o in user_history if o["timestamp"] == timestamp), None)
            return blob
        return None

    def get_message_content(self, msg: Message):
        logging.debug("message looks like %s", msg)
        content = {}
        if "message" in msg:
            content["text"] = msg["message"]
        elif "arg0" in msg:
            if "text" in msg:
                content["text"] = " ".join([msg["arg0"], msg["text"]])
            else:
                content["text"] = msg["arg0"]
        else:
            content["text"] = "None"
        content["reactions"] = " ".join(msg["reactions"])
        content["source"] = msg["source"]
        if "name" in msg:
            content["name"] = msg["name"]
        if "quote" in msg:
            content["quote"] = msg["quote"]
        return content

    async def handle_reaction(self, msg: Message) -> Response:
        """
        route a reaction to the original message.
        """
        assert isinstance(msg.reaction, Reaction)
        react = msg.reaction
        logging.debug("reaction from %s targeting %s", msg.source, react.ts)
        blob = await self.get_user_message(msg, react.ts)
        if blob:
            logging.debug("found target message %s", blob)
            user_history = await self.get_user_history(msg)
            i = user_history.index(blob)
            blob["reactions"].append(react.emoji)
            user_history[i] = blob
            user_id = self.get_user_id(msg)
            await self.msgs.set(user_id, user_history)
        return None

    async def save_sent_message(self, rpc_id: str, params: dict[str, Any]) -> None:
        result = await self.pending_requests[rpc_id]
        logging.debug("SENT: %s, %s", result, params)
        # Don't know how to find uuid in sent messages!
        if "recipient" in params:
            user = params["recipient"]
        if "group-id" in params:
            user = params["group-id"]
        logging.info("got user %s for blob %s", user, params)
        params["reactions"] = []
        params["timestamp"] = result.timestamp
        params["source"] = self.bot_number
        await self.msgs.extend(str(user), params)

    async def quote_chain(self, msg: dict) -> list[dict]:
        maybe_timestamp = msg.get("quote", {}).get("ts")
        if maybe_timestamp:
            maybe_quoted = await self.get_user_message(msg, maybe_timestamp)
            if maybe_quoted:
                return [msg] + await self.quote_chain(maybe_quoted)
        return [msg]

    async def do_q(self, msg: Message) -> Response:
        resp = ", ".join(str(m) for m in await self.quote_chain(msg.to_dict()))
        quote = {
            "quote-timestamp": msg.timestamp,
            "quote-author": msg.source,
            "quote-message": msg.full_text,
        }
        await self.respond(msg, resp, **quote)
        return None

    async def do_history(self, msg: Message) -> Response:
        user_history = await self.get_user_history(msg)
        return [self.get_message_content(blob) for blob in user_history]

    async def do_delete_history(self, msg: Message) -> Response:
        user = self.get_user_id(msg)
        await self.msgs.remove(user)
        return f"Deleted message history for user ID {user}"


if __name__ == "__main__":
    run_bot(MemoryBot)
