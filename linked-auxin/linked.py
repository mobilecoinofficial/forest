#!/usr/bin/python3.9
import ast
import asyncio
import logging
from decimal import Decimal
from typing import Any, Optional
import mc_util
from forest import utils
from forest.core import Message, PayBot, Response, app, hide, requires_admin
from mc_util import mob2pmob, pmob2mob

class LinkedAuxin(PayBot):
    async def default(self, message: Message) -> None:
        return None

async def pay_handler(req: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    amount = urllib.parse.unquote(request.query.get("amount", "0"))
    destination = urllib.parse.unquote(request.query.get("destination", ""))
    if amount and destination:
        await bot.send_payment(destination, mob2pmob(float(amount))
        return web.Response(status=200)
    return web.Response(status=400) 

app.add_routes([web.post("/pay", pay_handler)])

if __name__ == "__main__":
    app.add_routes([web.push()])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = LinkedAuxin()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
