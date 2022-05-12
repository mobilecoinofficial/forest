#!/usr/bin/python3.9
import asyncio
import logging
import urllib
from typing import Optional
from aiohttp import web

from forest import pghelp, utils
from forest.core import Message, QuestionBot, UserError, app
from mc_util import mob2pmob

activate = """To activate payments:

1. Open Signal, tap on the icon in the top left for Settings.
2. If you don’t see *Payments*, update Signal app: https://signal.org/install. If you still don't see it, reboot your phone. It can take a few hours.
3. Tap *Payments* and *Activate Payments*

For more information on Signal Payments visit:

https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""

PurseExpressions = pghelp.PGExpressions(
    table="purse_ledger",
    create_table="""CREATE TABLE purse_ledger (
        id SERIAL PRIMARY KEY,
        account TEXT,
        amount BIGINT,
        ts TIMESTAMP DEFAULT now(),
        memo TEXT,
        prompt_id BIGINT,
        tx_id TEXT);
    );""",
    add_tx="INSERT INTO {self.table} (account, amount, memo, tx_id, prompt_id) VALUES ($1, $2, $3, $4, $5);",
    stats="SELECT sum(amount)/1e12, count(id) FROM prompt_queue WHERE extract(second from now() - sent_ts) < $1",
)


class ImogenAuxin(QuestionBot):
    def __init__(self) -> None:
        self.ledger = pghelp.PGInterface(
            query_strings=PurseExpressions,
            database=utils.get_secret("DATABASE_URL"),
        )
        super().__init__()

    async def pay(
        self, recipient: str, amount_pmob: int, message: str, prompt_id: Optional[str]
    ) -> None:
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
                        await self.ledger.add_tx(
                            recipient,
                            amount_pmob,
                            message,
                            getattr(payment, "transaction_log_id", ""),
                            prompt_id,
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
    data = await request.post()
    string_amount = data.get("amount") or urllib.parse.unquote(
        request.query.get("amount", "0")
    )
    msg = data.get("message", "") or urllib.parse.unquote(
        request.query.get("message", "")
    )
    destination = data.get("destination") or urllib.parse.unquote(
        request.query.get("destination", "")
    )
    string_prompt_id = data.get("prompt_id") or request.query.get("prompt_id")
    try:
        prompt_id: Optional[int] = int(string_prompt_id)  # type: ignore
    except (ValueError, TypeError):
        prompt_id = None
    try:
        amount = mob2pmob(float(string_amount))  # type: ignore
        asyncio.create_task(bot.pay(destination, amount, msg, prompt_id))
        return web.Response(status=200)
    # fixme: restructure to not catch ValueError in pay
    except ValueError:
        pass
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
