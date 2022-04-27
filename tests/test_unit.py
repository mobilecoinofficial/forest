import asyncio
import logging
import os
import pathlib
from importlib import reload
import pytest
import pytest_asyncio

# Prevent Utils from importing dev_secrets by default
os.environ["ENV"] = "test"

from forest import utils, core
from forest.core import Message, Response
from tests.mockbot import MockBot

# Sample bot number alice
BOT_NUMBER = "+11111111111"
USER_NUMBER = "+22222222222"


def test_secrets(tmp_path: pathlib.Path) -> None:
    """Tests that utils.get_secret reads the values of dev_secrets properly"""
    open(tmp_path / "test_secrets", "w", encoding="utf-8").write("A=B\nC=D")
    os.chdir(tmp_path)
    reload(utils)

    assert utils.get_secret("A") == "B"
    assert utils.get_secret("C") == "D"
    assert utils.get_secret("E") == ""


def test_root(tmp_path: pathlib.Path) -> None:
    """Tests the Root Dir Logic"""
    os.chdir(tmp_path)

    # Test that ROOT_DIR is . when running locally
    assert reload(utils).ROOT_DIR == "."

    # Test that when Download is set to 1 and so downloading datastore from postgress,
    # the Root Dir is /tmp/local-signal
    open(tmp_path / "test_secrets", "w", encoding="utf-8").write("DOWNLOAD=1")
    assert reload(utils).ROOT_DIR == "/tmp/local-signal"

    # Tests that when a Fly App Name is specified, therefore it must be running on fly,
    # the Root Dir is /app
    os.environ["FLY_APP_NAME"] = "A"
    assert reload(utils).ROOT_DIR == "/app"


# https://github.com/pytest-dev/pytest-asyncio/issues/68
# all async tests and fixtures implicitly use event_loop, which has scope "function" by default
# so if we want bot to have scope "session" (so it's not destroyed and created between tests),
# all the fixtures it uses need to have at least "session" scope
@pytest.fixture()
def event_loop(request):
    """Fixture version of the event loop"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def bot():
    """Bot Fixture allows for exiting gracefully"""
    bot = MockBot(BOT_NUMBER)
    yield bot
    bot.sigints += 1
    bot.exiting = True
    bot.handle_messages_task.cancel()
    await bot.client_session.close()
    await core.pghelp.close_pools()


@pytest.mark.asyncio
async def test_commands(bot) -> None:
    """Tests commands"""
    # Enable Magic allows for mistyped commands
    os.environ["ENABLE_MAGIC"] = "1"

    # Tests do_ping with a mistyped command, expecting "/pong foo"
    assert await bot.get_cmd_output("/pingg foo") == "/pong foo"

    # tests the uptime command just checks to see if it starts with "Uptime: "
    assert (await bot.get_cmd_output("uptime")).startswith("Uptime: ")

    # tests that eval only works for admins
    assert (
        await bot.get_cmd_output("/eval 1+1")
    ) == "you must be an admin to use this command"

    print(await bot.get_cmd_output("/help"))

    assert (await bot.get_cmd_output("/help")).startswith("Documented commands:")

    # test the default behaviour
    assert (await bot.get_cmd_output("gibberish two")).startswith(
        "That didn't look like a valid command"
    )


@pytest.mark.asyncio
async def test_questions(bot) -> None:
    """Tests the various questions from questionbot class"""

    # Enable Magic allows for mistyped commands
    os.environ["ENABLE_MAGIC"] = "1"
    # the issue here is that we need to send "yes" *after* the question has been asked
    # so we make it as create_task, then send the input, then await the task to get the result

    answer = asyncio.create_task(
        bot.ask_yesno_question(USER_NUMBER, "Do you like faeries?")
    )
    await bot.send_input("yes")
    assert await answer is True

    answer = asyncio.create_task(
        bot.ask_freeform_question(USER_NUMBER, "What's your favourite tree?")
    )
    await bot.send_input("Birch")
    assert await answer == "Birch"

    answer = asyncio.create_task(
        bot.ask_freeform_question(USER_NUMBER, "What's your favourite tree?")
    )

    question_text = "What is your tshirt size?"
    options = {"S": "", "M": "", "L": "", "XL": "", "XXL": ""}

    choice = asyncio.create_task(
        bot.ask_multiple_choice_question(
            USER_NUMBER, question_text, options, require_confirmation=False
        )
    )
    await bot.send_input("M")
    assert await choice == "M"

    choice = asyncio.create_task(
        bot.ask_multiple_choice_question(
            USER_NUMBER, question_text, options, require_confirmation=True
        )
    )
    await bot.send_input("XXL")
    await asyncio.sleep(0)
    await bot.send_input("yes")
    assert await choice == "XXL"
