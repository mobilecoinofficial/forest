import logging
from typing import Optional, Union
from forest.core import JSON, Bot, Message, Response, run_bot
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

    def get_user_id(self, msg: Union[Message, JSON]) -> str:
        if isinstance(msg, dict):
            if "source" in msg:
                if "group-id" in msg:
                    user = msg["group-id"]
                user = msg["source"]
        else:
            user = msg.source
            if msg.group:
                user = str(msg.group)
        return user

    async def get_user_history(self, user: str) -> Union[list[JSON], None]:
        user_history = await self.msgs.get(user)
        if user_history:
            return user_history
        return None

    async def get_user_message(self, msg: JSON, timestamp: str) -> Union[JSON, None]:
        user = self.get_user_id(msg)
        user_history = await self.get_user_history(user)
        if user_history:
            blob = next(
                (o for o in user_history if o["timestamp"] == timestamp),
                None,
            )
            if blob:
                return blob
        return None

    # This maybe doesn't work with auxin?
    async def handle_reaction(self, msg: Message) -> Response:
        """
        route a reaction to the original message.
        """
        assert isinstance(msg.reaction, Reaction)
        react = msg.reaction
        logging.debug("reaction from %s targeting %s", msg.source, react.ts)
        blob = await self.get_user_message(msg, react.ts)
        if blob:
            user = self.get_user_id(msg)
            user_history = await self.get_user_history(user)
            i = user_history.index(blob)
            blob["reactions"].append(react.emoji)
            user_history[i] = blob
            user_id = self.get_user_id(msg)
            await self.msgs.set(user_id, user_history)
        return None

    async def save_sent_message(self, rpc_id: str, params: JSON) -> None:
        """
        save own messages for each channel
        """
        result = await self.pending_requests[rpc_id]
        if "recipient" in params:
            user = params["recipient"]
        if "group-id" in params:
            user = params["group-id"]
        params["reactions"] = []
        params["timestamp"] = result.timestamp
        params["source"] = self.bot_number
        await self.msgs.extend(str(user), params)

    async def quote_chain(self, msg: JSON) -> list[JSON]:
        maybe_timestamp = msg.get("quote", {}).get("ts")
        if maybe_timestamp:
            maybe_quoted = await self.get_user_message(msg, maybe_timestamp)
            if maybe_quoted:
                return [msg] + await self.quote_chain(maybe_quoted)
        return [msg]

    # Useful for testing but also for getting a little clean dict of content
    def get_message_content(self, msg: JSON) -> JSON:
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
        if "reactions" in content:
            content["reactions"] = " ".join(msg["reactions"])
        content["source"] = msg["source"]
        if "name" in msg:
            content["name"] = msg["name"]
        if "quote" in msg:
            content["quote"] = msg["quoted_text"]
        return content

    # For testing recursive quotes
    async def do_q(self, msg: Message) -> Response:
        resp = ", ".join(str(m) for m in await self.quote_chain(msg.to_dict()))
        quote = {
            "quote-timestamp": msg.timestamp,
            "quote-author": msg.source,
            "quote-message": msg.full_text,
        }
        await self.respond(msg, resp, **quote)
        return None

    # This can be annoying, maybe should be behind @requires_admin
    async def do_history(self, msg: Message) -> Response:
        user = self.get_user_id(msg)
        user_history = await self.get_user_history(user)
        if user_history:
            return [self.get_message_content(blob) for blob in user_history]
        return None

    async def do_clear_history(self, msg: Message) -> Response:
        user = self.get_user_id(msg)
        await self.msgs.remove(user)
        return f"Deleted message history for user ID {user}"


if __name__ == "__main__":
    run_bot(MemoryBot)
