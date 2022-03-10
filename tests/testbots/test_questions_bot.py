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

        _, choice = await self.ask_multiple_choice_question(
            message.source, question_text, options, requires_confirmation=False
        )
        if choice == "Faeries":
            return "Faeries are my favourite too c:"
        return f"I think {choice} are super neat too!"


if __name__ == "__main__":
    run_bot(TestBot)
