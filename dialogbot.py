#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team

import json
from typing import Optional
from forest.core import Bot, Message, Response, run_bot, utils
from google.cloud import dialogflow


class DialogBot(Bot):
    def __init__(self, bot_number: Optional[str] = None) -> None:
        self.credentials = utils.get_secret("GOOGLE_APPLICATION_CREDENTIALS")
        self.session_client = dialogflow.SessionsClient().from_service_account_json(
            self.credentials
        )
        with open(self.credentials, "r") as f:
            secrets = json.load(f)
            self.agent_id = secrets["project_id"]
        super().__init__(bot_number)

    async def handle_message(self, message: Message) -> Response:
        # try to get a direct match, or a fuzzy match if appropriate
        if cmd := self.match_command(message):
            # invoke the function and return the response
            return await getattr(self, "do_" + cmd)(message)
        if message.text == "TERMINATE":
            return "signal session reset"
        if message.full_text:
            session = self.session_client.session_path(
                self.agent_id, message.source[1:]
            )
            text_input = dialogflow.TextInput(
                text=message.full_text, language_code="en"
            )
            query_input = dialogflow.QueryInput(text=text_input)

            response = self.session_client.detect_intent(
                request={"session": session, "query_input": query_input}
            )

            return response.query_result.fulfillment_text
        return await self.default(message)

    async def do_template(self, _: Message) -> str:
        """
        A template you can fill in to make your own bot. Anything after do_ is a / command.
        Return value is used to send a message to the user.
        """
        return "template."

    async def do_hello(self, _: Message) -> str:
        """
        Simple, Hello, world program. Type /hello and the bot will say "Hello, world!"

        """
        return "Hello, world!"

    async def do_echo(self, message: Message) -> str:
        """
        Repeats what you said. Type /echo foo and the bot will say "foo".
        """
        return message.text


if __name__ == "__main__":
    run_bot(DialogBot)
