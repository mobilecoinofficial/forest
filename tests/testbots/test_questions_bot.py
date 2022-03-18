from forest.core import QuestionBot, Message, run_bot, Response


class TestBot(QuestionBot):
    """Bot that has tests for every type of question"""

    # async def do_test_ask_multiple(self, message:Message) -> None:

    async def do_test_ask_yesno_question(self, message: Message) -> Response:
        """Asks a sample Yes or No question"""

        if await self.ask_yesno_question(message.source, "Do you like faeries?"):
            return "That's cool, me too!"
        return "Aww :c"

    async def do_test_multiple_choice_list_no_confirm(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with list and no confirmation"""

        question_text = "What is your favourite forest creature?"
        options = ["Deer", "Foxes", "Faeries", "Crows"]

        choice = await self.ask_multiple_choice_question(
            message.source, question_text, options, require_confirmation=False
        )
        if choice and choice == "Faeries":
            return "Faeries are my favourite too c:"

        if choice:
            return f"I think {choice} are super neat too!"

        return "oops, sorry"

    async def do_test_multiple_choice_list_with_confirm(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with list and confirmation"""

        question_text = "What is your favourite forest creature?"
        options = ["Deer", "Foxes", "Faeries", "Crows"]

        choice = await self.ask_multiple_choice_question(
            message.source, question_text, options
        )
        if choice and choice == "Faeries":
            return "Faeries are my favourite too c:"

        if choice:
            return f"I think {choice} are super neat too!"

        return "oops, sorry"

    async def do_test_multiple_choice_dict_with_confirm(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with dict and confirmation"""

        question_text = "What is your favourite forest creature?"
        options = {"A": "Deer", "B": "Foxes", "⛧": "Faeries", "D": "Crows"}

        choice = await self.ask_multiple_choice_question(
            message.source, question_text, options, require_confirmation=True
        )
        if choice and choice == "Faeries":
            return "Faeries are my favourite too c:"

        if choice:
            return f"I think {choice} are super neat too!"

        return "oops, sorry"

    async def do_test_multiple_choice_dict_no_confirm(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with a dict and no confirmation"""

        question_text = "What is your favourite forest creature?"
        options = {"A": "Deer", "B": "Foxes", "⛧": "Faeries", "D": "Crows"}

        choice = await self.ask_multiple_choice_question(
            message.source, question_text, options, require_confirmation=False
        )
        if choice and choice == "Faeries":
            return "Faeries are my favourite too c:"

        if choice:
            return f"I think {choice} are super neat too!"

        return "oops, sorry"

    async def do_test_multiple_choice_dict_emptyval(self, message: Message) -> Response:
        """Asks a Sample Multiple Choice question with a dict with empty values"""

        question_text = "What is your tshirt size?"
        options = {"S": "", "M": "", "L": "", "XL": "", "XXL": ""}

        choice = await self.ask_multiple_choice_question(
            message.source, question_text, options, require_confirmation=True
        )
        if choice:
            return choice
        return "oops, sorry"

    async def do_test_multiple_choice_dict_mostly_emptyval(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with a dict with mostly empty values"""

        question_text = "What is your tshirt size?"
        options = {"S": "", "M": "M", "L": "", "XL": "", "XXL": ""}

        choice = await self.ask_multiple_choice_question(
            message.source, question_text, options, require_confirmation=True
        )
        if choice:
            return choice
        return "oops, sorry"

    async def do_test_address_question_no_confirmation(
        self, message: Message
    ) -> Response:
        """Asks a sample address question"""

        address = await self.ask_address_question(message.source)

        if address:
            return address
        return "oops, sorry"

    async def do_test_address_question_with_confirmation(
        self, message: Message
    ) -> Response:
        """Asks a sample address question"""

        address = await self.ask_address_question(
            message.source, require_confirmation=True
        )

        if address:
            return address
        return "oops, sorry"


if __name__ == "__main__":
    run_bot(TestBot)
