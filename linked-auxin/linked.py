#!/usr/bin/python3.9
import urllib
from aiohttp import web

from forest.core import Message, PayBot, app
from mc_util import mob2pmob


class LinkedAuxin(PayBot):
    async def send_payment(  # pylint: disable=too-many-locals
        self,
        recipient: str,
        amount_pmob: int,
        receipt_message: str = "Transaction sent!",
        **params: Any,
    ) -> Optional[Message]:
        try:
            return await super().send_payment(recipient, amount_pmob, receipt_message)
        except UserError:
            pass
            # launch conversion script...

    async def default(self, message: Message) -> None:
        return None


async def pay_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    destination = urllib.parse.unquote(request.query.get("destination", ""))
    amount = urllib.parse.unquote(request.query.get("amount", "0"))
    msg = urllib.parse.unquote(request.query.get("message", ""))
    if amount and destination:
        await bot.send_payment(destination, mob2pmob(float(amount)), msg)
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
        out_app["bot"] = LinkedAuxin()

    web.run_app(app, port=8081, host="0.0.0.0")
