import pytest
import datastore
import subprocess

async def test_datastore() -> None
    # deploy
    subprocess.run("fly deploy", shell=True)
    sess = main.Session(bot_number=None)
    await sess.launch_and_connect()
    # send a message from a different account
    await sess.send_message(utils.get_secret("BOT_NUMBER"), "/echo spam"))
    for msg in await sess.signalcli_output_iter():
        assert msg.text = "spam"
        break
    # deploy
    # might need to tweak something for fly to redeploy? maybe change a secret?
    subprocess.run("fly deploy", shell=True)
    # send another message
    await sess.send_message(utils.get_secret("BOT_NUMBER"), "/echo ham"))
    for msg in await sess.signalcli_output_iter():
        # expect it to work
        assert msg.text = "ham"
        break
