import asyncio
import os
import pytest
import pytest_asyncio


# Prevent Utils from importing dev_secrets by default
os.environ["ENV"] = "test"


from forest.core import Message, run_bot, Response
from forest import core
from tests.mockbot import MockBot, Tree, QuestionBot

# Sample bot number alice
BOT_NUMBER = "+11111111111"
USER_NUMBER = "+22222222222"


# class TestBot(QuestionBot):
class TestBot(MockBot):
    """Bot that has tests for every type of question"""

    # async def do_test_ask_multiple(self, message:Message) -> None:

    async def do_test_ask_yesno_question(self, message: Message) -> Response:
        """Asks a sample Yes or No question"""

        if await self.ask_yesno_question(
            (message.uuid, message.group), "Do you like faeries?"
        ):
            return "That's cool, me too!"
        return "Aww :c"

    async def do_test_multiple_choice_list_no_confirm(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with list and no confirmation"""

        question_text = "What is your favourite forest creature?"
        options = ["Deer", "Foxes", "Faeries", "Crows"]

        choice = await self.ask_multiple_choice_question(
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=False,
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
            (message.uuid, message.group), question_text, options
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
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=True,
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
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=False,
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
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=True,
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
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=True,
        )
        if choice:
            return choice
        return "oops, sorry"

    async def do_test_address_question_no_confirmation(
        self, message: Message
    ) -> Response:
        """Asks a sample address question"""

        address = await self.ask_address_question((message.uuid, message.group))

        if address:
            return address
        return "oops, sorry"

    async def do_test_address_question_with_confirmation(
        self, message: Message
    ) -> Response:
        """Asks a sample address question"""

        address = await self.ask_address_question(
            (message.uuid, message.group), require_confirmation=True
        )

        if address:
            return address
        return "oops, sorry"

    async def do_test_ask_freeform_question(self, message: Message) -> Response:
        """Asks a sample freeform question"""

        answer = await self.ask_freeform_question(
            (message.uuid, message.group), "What's your favourite tree?"
        )

        if answer:
            return f"No way! I love {answer} too!!"
        return "oops, sorry"


@pytest_asyncio.fixture()
async def bot():
    """Bot Fixture allows for exiting gracefully"""
    bot = TestBot(BOT_NUMBER)
    yield bot
    bot.sigints += 1
    bot.exiting = True
    bot.handle_messages_task.cancel()
    await bot.client_session.close()
    await core.pghelp.pool.close()


@pytest.mark.asyncio
async def test_dialog(bot) -> None:
    """Tests the bot by running a dialogue"""
    dialogue = [
        ["test_ask_yesno_question", "Do you like faeries?"],
        ["yes", "That's cool, me too!"],
    ]

    for line in dialogue:
        assert await bot.get_cmd_output(line[0]) == line[1]


@pytest.mark.asyncio
async def test_yesno_tree(bot) -> None:
    """Tests the bot by running a tree"""
    tree = Tree(
        ["test_ask_yesno_question", "Do you like faeries?"],
        [Tree(["yes", "That's cool, me too!"]), Tree(["no", "Aww :c"])],
    )
    tests = tree.get_all_paths()

    for test in tests:
        for subtest in test:
            assert await bot.get_cmd_output(subtest[0]) == subtest[1]


if __name__ == "__main__":
    run_bot(TestBot)
