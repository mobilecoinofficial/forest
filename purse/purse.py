#!/usr/bin/python3.9
import logging
import urllib
import asyncio
from aiohttp import web

from forest import pdictng
from forest.core import Message, QuestionBot, UserError, app
from mc_util import mob2pmob

activate = """To activate payments:

1. Open Signal, tap on the icon in the top left for Settings.
2. If you don’t see *Payments*, update Signal app: https://signal.org/install. If you still don't see it, reboot your phone. It can take a few hours.
3. Tap *Payments* and *Activate Payments*

For more information on Signal Payments visit:

https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""


class ImogenAuxin(QuestionBot):
    def __init__(self) -> None:
        self.payments = pdictng.aPersistDict("payments")
        super().__init__()

    async def pay(self, recipient: str, amount_pmob: int, message: str) -> None:
        for i in range(3):
            if await self.get_address(recipient):
                try:
                    payment = await self.send_payment(
                        recipient,
                        amount_pmob,
                        message,
                        confirm_tx_timeout=5,
                        comment=f"prompt payment to {recipient}",
                    )
                    if payment and payment.status == "tx_status_succeeded":
                        await self.payments.extend(
                            recipient,
                            [
                                recipient,
                                amount_pmob,
                                message,
                                getattr(payment, "transaction_log_id", ""),
                            ],
                        )
                        break
                except UserError:
                    pass
            payments = await self.ask_yesno_question(
                recipient,
                (
                    "I'm trying to send you a tip for your popular prompt with Imogen, but couldn't get your MobileCoin address.\n\n"
                    "Do you have payments enabled?"
                ),
            )
            if payments:
                if not await self.get_address(recipient):
                    # await self.send_message(recipient,"Hmm, I still can't get your address, could you message me from your phone?")
                    # answer_future = self.pending_answers[recipient] = asyncio.Future()
                    # answer = await answer_future
                    # self.pending_answers.pop(recipient)
                    # if int(answer.device_id) != 1: pass
                    await self.ask_freeform_question(
                        recipient,
                        "Hmm, I still can't get your address, could you message me from your phone? If this issue persists, try deactivating and reactivating payments.",
                    )
            else:
                await self.send_message(recipient, activate)

    async def default(self, message: Message) -> None:
        return None


async def pay_handler(request: web.Request) -> web.Response:
    logging.info("got pay request")
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    destination = urllib.parse.unquote(request.query.get("destination", ""))
    amount = urllib.parse.unquote(request.query.get("amount", "0"))
    msg = urllib.parse.unquote(request.query.get("message", ""))
    if amount and destination:
        asyncio.create_task(bot.pay(destination, mob2pmob(float(amount)), msg))
        return web.Response(status=200)
    return web.Response(status=400)


async def award_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    destination = urllib.parse.unquote(request.query.get("destination", ""))
    amount = float(urllib.parse.unquote(request.query.get("percent", "0.2")))
    if amount and destination:
        award = int(amount * await bot.mobster.get_balance())
        await bot.send_payment(destination, award)
        return web.Response(status=200)
    return web.Response(status=400)


app.add_routes([web.post("/pay", pay_handler)])
app.add_routes([web.post("/award", award_handler)])

if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = ImogenAuxin()

    web.run_app(app, port=8080, host="0.0.0.0")
