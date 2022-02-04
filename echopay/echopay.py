#!/usr/bin/python3.9
import logging

from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary

from forest.core import Message, PayBot, Response, app

britbot = "+447888866969"
fee = int(1e12 * 0.0004)

REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class AuthorizedPayer(PayBot):
    no_repay: list[str] = []

    async def handle_message(self, message: Message) -> Response:
        if "hook me up" in message.text.lower():
            return await self.do_pay(message)
        return await super().handle_message(message)

    async def do_no_repay(self, msg: Message) -> Response:
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
            return "will repay you"
        self.no_repay.append(msg.source)
        return "won't repay you"

    @time(REQUEST_TIME)  # type: ignore
    async def do_pay(self, msg: Message) -> Response:
        payment_notif_sent = await self.send_payment(msg.source, int(1e9))
        if payment_notif_sent:
            logging.info(payment_notif_sent)
            delta = (payment_notif_sent.timestamp - msg.timestamp) / 1000
            await self.admin(f"payment delta: {delta}")
            self.signal_roundtrip_latency.append((msg.timestamp, "payment", delta))
        return None

    @time(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        payment_notif = await self.send_payment(msg.source, amount_pmob - fee)
        if not payment_notif:
            return None
        delta = (payment_notif.timestamp - msg.timestamp) / 1000
        self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
        await self.admin(f"repayment delta: {delta}")
        return None


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = AuthorizedPayer()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
