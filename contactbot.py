#!/usr/bin/python3.9
import asyncio
import json
import logging
import random
import time
from collections import defaultdict
from typing import Optional, Union

import aiohttp
from aiohttp import web

import teli
from forest import payments_monitor, utils
from forest.core import Bot, Message, Response, app
from forest_tables import GroupRoutingManager, PaymentsManager, RoutingManager


class Forest(Bot):
    def __init__(self, *args: str) -> None:
        self.teli = teli.Teli()
        self.balances: dict[str, float] = defaultdict(lambda: 0.0)
        self.payments_manager = PaymentsManager()
        self.routing_manager = RoutingManager()
        super().__init__(*args)

    async def send_sms(
        self, source: str, destination: str, message_text: str
    ) -> dict[str, str]:
        """Send SMS via teleapi.net call and returns the response"""
        payload = {
            "source": source,
            "destination": destination,
            "message": message_text,
        }
        response = await self.client_session.post(
            "https://api.teleapi.net/sms/send?token=" + utils.get_secret("TELI_KEY"),
            data=payload,
        )
        response_json_all = await response.json()
        response_json = {
            k: v
            for k, v in response_json_all.items()
            if k in ("status", "segment_count")
        }
        # hide how the sausage is made
        return response_json

    async def get_user_numbers(self, message: Message) -> list[str]:
        """List the teli numbers a user owns"""
        if message.source:
            maybe_routable = await self.routing_manager.get_id(message.source)
            return [registered.get("id") for registered in maybe_routable]
        return []

    async def handle_message(self, message: Message) -> Response:
        """Handle an invidiual Message from Signal.
        If it's a group creation blob, make a new routing rule from it.
        If it's a group message, route it to the relevant conversation.
        If it's a payment, deal with that separately.
        Otherwise, use the default Bot routing to do_x methods
        """
        if "group" in message.blob:
            # SMS with {number} via {number}
            their, our = message.blob["name"].removeprefix("SMS with ").split(" via ")
            # TODO: this needs to use number[0]
            await GroupRoutingManager().set_sms_route_for_group(
                teli.teli_format(their),
                teli.teli_format(our),
                message.blob["group"],
            )
            # cmd = {
            #     "command": "updateGroup",
            #     "group": message.blob["group"],
            #     "admin": message.source,
            # }
            logging.info("made a new group route from %s", message.blob)
            return None
        numbers = await self.get_user_numbers(message)
        if numbers and message.group and message.text:
            group = await group_routing_manager.get_sms_route_for_group(message.group)
            if group:
                await self.send_sms(
                    source=group[0].get("our_sms"),
                    destination=group[0].get("their_sms"),
                    message_text=message.text,
                )
                await self.send_reaction(message, "\N{Outbox Tray}")
                return None
            logging.warning("couldn't find the route for this group...")
        elif numbers and message.quoted_text:
            try:
                quoted = dict(
                    line.split(":\t", 1) for line in message.quoted_text.split("\n")
                )
            except ValueError:
                quoted = {}
            if quoted.get("destination") in numbers and quoted.get("source"):
                logging.info("sms destination from quote: %s", quoted["destination"])
                response = await self.send_sms(
                    source=quoted["destination"],
                    destination=quoted["source"],
                    message_text=message.text,
                )
                await self.send_reaction(message, "\N{Outbox Tray}")
                return response
            await self.send_reaction(message, "\N{Cross Mark}")
            return "Couldn't send that reply"
        if  message.payment:
            return await self.handle_payment(message)
        return await Bot.handle_message(self, message)

    async def do_help(self, _: Message) -> str:
        # TODO: https://github.com/forestcontact/forest-draft/issues/14
        return (
            "Welcome to the Forest.contact Pre-Release!\n"
            "To get started, try /register, or /status! "
            "If you've already registered, try to send a message via /send."
            ""
        )

    async def do_send(self, message: Message) -> Union[str, dict]:
        """Send an SMS message. Usage: /send <destination> <message>
        """
        numbers = await self.get_user_numbers(message)
        if not numbers:
            return "You don't have any numbers. Register with /register"
        sms_dest = await self.check_target_number(message)
        if not sms_dest:
            return "Couldn't parse that number"
        response = await self.send_sms(
            source=numbers[0],
            destination=sms_dest,
            message_text=message.text,
        )
        await self.send_reaction(message, "\N{Outbox Tray}")
        # sms_uuid = response.get("data")
        # TODO: store message.source and sms_uuid in a queue, enable https://apidocs.teleapi.net/api/sms/delivery-notifications
        #    such that delivery notifs get redirected as responses to send command
        return response

    do_msg = do_send

    async def do_mkgroup(self, message: Message) -> str:
        """Create a group for your SMS messages with a given recipient.
        Messages from that recipient will be posted in that group instead of sent to you.
        Messages sent in that group will be sent to that recipient.
        You can add other Signal users; they'll be able to use your number as well
        """
        numbers = await self.get_user_numbers(message)
        target_number = await self.check_target_number(message)
        if not numbers:
            return "no"
        if not target_number:
            return ""
        cmd = {
            "output": "json",
            "command": "updateGroup",
            "member": [message.source],
            "admin": [message.source],
            "name": f"SMS with {target_number} via {numbers[0]}",
        }
        await self.signalcli_input_queue.put(cmd)
        await self.send_reaction(message, "\N{Busts In Silhouette}")
        return "invited you to a group"

    do_query = do_mkgroup
    if not utils.get_secret("GROUPS"):
        del do_mkgroup, do_query

    async def handle_payment(self, message: Message) -> str:
        """Decode the receipt, then update balances"""
        # TODO: use the ledger table
        logging.info(message.payment)
        amount = await payments_monitor.get_receipt_amount(message.payment["receipt"])
        if amount is None:
            return "That looked like a payment, but we couldn't parse it"
        self.balances[message.source] += amount
        await self.respond(message, f"Thank you for sending {amount} MOB")
        diff = self.balances[message.source] - await self.get_mob_price()
        if diff < 0:
            return f"Please send another {abs(diff)} MOB to buy a phone number"
        if diff == 0:
            return "Thank you for paying! You can now buy a phone number with /order <area code>"
        return "Thank you for paying! You've overpayed by {diff}. Contact an administrator for a refund"

    async def do_status(self, message: Message) -> Union[list[str], str]:
        """List numbers if you have them. Usage: /status"""
        numbers: list[str] = [
            registered.get("id")
            for registered in await self.routing_manager.get_id(message.source)
        ]
        # paid but not registered
        if self.balances[message.source] > 0 and not numbers:
            return [
                "Welcome to the beta! Thank you for your payment. Please contact support to finish setting up your account by requesting to join this group. We will reach out within 12 hours.",
                "https://signal.group/#CjQKINbHvfKoeUx_pPjipkXVspTj5HiTiUjoNQeNgmGvCmDnEhCTYgZZ0puiT-hUG0hUUwlS",
                #    "Alternatively, try /order <area code>",
            ]
        if numbers and len(numbers) == 1:
            # registered, one number
            return f'Hi {message.name}! We found {numbers[0]} registered for your user. Try "/send {message.source} Hello from Forest Contact via {numbers[0]}!".'
        # registered, many numbers
        if numbers:
            return f"Hi {message.name}! We found several numbers {numbers} registered for your user. Try '/send {message.source} Hello from Forest Contact via {numbers[0]}!'."
        # not paid, not registered
        return (
            "We don't see any Forest Contact numbers for your account!"
            " If you would like to register a new number, "
            'try "/register" and following the instructions.'
        )

    rate_cache: tuple[int, Optional[float]] = (0, None)

    async def get_rate(self) -> float:
        """Get the current USD/MOB price and cache it for an hour"""
        hour = round(time.time() / 3600)  # same value within each hour
        if self.rate_cache[0] == hour and self.rate_cache[1] is not None:
            return self.rate_cache[1]
        try:
            url = "https://big.one/api/xn/v1/asset_pairs/8e900cb1-6331-4fe7-853c-d678ba136b2f"
            last_val = await self.client_session.get(url)
            resp_json = await last_val.json()
            mob_rate = float(resp_json.get("data").get("ticker").get("close"))
        except (
            aiohttp.ClientError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ) as e:
            logging.error(e)
            # big.one goes down sometimes, if it does... make up a price
            mob_rate = 14
        self.rate_cache = (hour, mob_rate)
        return mob_rate

    async def get_mob_price(self, perturb: bool = False) -> float:
        mob_rate = await self.get_rate()
        usdt_price = 4.0  # 15.00
        if perturb:
            # perturb each price slightly to have a unique payment
            mob_rate -= random.random() / 1000
        # invpico = 100000000000 # doesn't work in mixin
        invnano = 100000000
        nmob_price = int(usdt_price / mob_rate * invnano)
        mob_price_exact = round(nmob_price / invnano, 3)
        # dunno if we want to generate new wallets? what happens if a user overpays?
        return mob_price_exact

    async def do_register(self, message: Message) -> bool:
        """register for a phone number"""
        mob_price_exact = await self.get_mob_price()
        nmob_price = mob_price_exact * 100000000
        responses = [
            f"The current price for a SMS number is {mob_price_exact}MOB/month. If you would like to continue, please send exactly...",
            f"{mob_price_exact}",
            "on Signal Pay, or to",
            "nXz8gbcAfHQQUwTHuQnyKdALe5oXKppDn9oBRms93MCxXkiwMPnsVRp19Vrmb1GX6HdQv7ms83StXhwXDuJzN9N7h3mzFnKsL6w8nYJP4q",
            "Upon payment, you will be able to select the area code for your new phone number!",
        ]
        await self.send_message(message.source, responses)
        # check for payments every 10s for 1hr
        for _ in range(360):
            payment_done = await self.payments_manager.get_payment(nmob_price * 1000)
            if payment_done:
                payment_done = payment_done[0]
                await self.send_message(
                    message.source,
                    [
                        "Thank you for your payment! Please save this transaction ID for your records and include it with any customer service requests. Without this payment ID, it will be harder to verify your purchase.",
                        f"{payment_done.get('transaction_log_id')}",
                        'Please finish setting up your account at your convenience with the "/status" command.',
                    ],
                )
                self.balances[message.source] += payment_done.get("value_pmob")
                return True
            await asyncio.sleep(10)
        return False

    async def do_balance(self, message: Message) -> str:
        """Check your balance"""
        return f"Your balance is {self.balances[message.source]} MOB"

    async def do_pay(self, message: Message) -> str:
        if message.arg1 == "shibboleth":
            balance = self.balances.get(message.source, 0)
            new_balance = balance + await self.get_mob_price()
            self.balances[message.source] += new_balance
            return "...thank you for your payment"
        if message.arg1 == "sibboleth":
            return "sending attack drones to your location"
        return "no"

    async def do_order(self, msg: Message) -> str:
        """Usage: /order <area code>"""
        if not (msg.arg1 and len(msg.arg1) == 3 and msg.arg1.isnumeric()):
            return """Usage: /order <area code>"""
        price = await self.get_mob_price()
        diff = self.balances[msg.source] - price
        if diff < 0:
            # this needs to check if there are *unfulfilled* payments
            return "Make a payment with Signal Pay or /register first"
        await self.routing_manager.sweep_expired_destinations()
        available_numbers = [
            num
            for record in await self.routing_manager.get_available()
            if (num := record.get("id")).startswith(msg.arg1)
        ]
        if available_numbers:
            number = available_numbers[0]
            await self.send_message(msg.source, f"Found {number} for you...")
        else:
            numbers = await self.teli.search_numbers(area_code=msg.arg1, limit=1)
            if not numbers:
                return "Sorry, no numbers for that area code"
            number = numbers[0]
            await self.send_message(msg.source, f"Found {number}")
            await self.routing_manager.intend_to_buy(number)
            buy_info = await self.teli.buy_number(number)
            await self.send_message(msg.source, f"Bought {number}")
            if "error" in buy_info:
                await self.routing_manager.delete(number)
                return f"Something went wrong: {buy_info}"
            await self.routing_manager.mark_bought(number)
        await self.teli.set_sms_url(number, utils.URL + "/inbound")
        await self.routing_manager.set_destination(number, msg.source)
        if await self.routing_manager.get_destination(number):
            self.balances[msg.source] -= price
            return f"You are now the proud owner of {number}"
        return "Database error?"

    if not utils.get_secret("ORDER"):
        del do_order, do_pay

    async def start_process(self) -> None:
        """Make sure full-service has a wallet before starting signal"""
        try:
            await payments_monitor.get_address()
        except IndexError:
            await payments_monitor.import_account()
        if utils.get_secret("MIGRATE"):
            await self.migrate()
        await super().start_process()

    async def migrate(self) -> None:
        """Add a status column to routing, make sure all destinations are E164,
        make sure teli is using the right URL for all of our numbers,
        recreate the group_routing table, and add a datastore column to signal_accounts
        """
        logging.info("migrating db...")
        await self.routing_manager.migrate()
        rows = await self.routing_manager.execute("SELECT id, destination FROM routing")
        for row in rows if rows else []:
            if not utils.LOCAL:
                await self.teli.set_sms_url(row.get("id"), utils.URL + "/inbound")
            if (dest := row.get("destination")) :
                new_dest = utils.signal_format(dest)
                await self.routing_manager.set_destination(row.get("id"), new_dest)
        await self.datastore.account_interface.migrate()
        await group_routing_manager.execute("DROP TABLE IF EXISTS group_routing")
        await group_routing_manager.create_table()


async def inbound_sms_handler(request: web.Request) -> web.Response:
    """Handles SMS messages received by our numbers.
    Try groups, then try users, otherwise fall back to an admin
    """
    session = request.app.get("bot")
    msg_data: dict[str, str] = dict(await request.post())  # type: ignore
    if not session:
        # no live worker sessions
        # if we can't get a signal delivery receipt/bad session, we could
        # return non-200 and let teli do our retry
        # however, this would require awaiting output from signal; tricky
        await request.app["client_session"].post(
            "https://counter.pythia.workers.dev/post", data=msg_data
        )
        return web.Response(status=504, text="Sorry, no live workers.")
    sms_destination = msg_data.get("destination")
    # lookup sms recipient to signal recipient
    maybe_signal_dest = await RoutingManager().get_destination(sms_destination)
    maybe_group = await group_routing_manager.get_group_id_for_sms_route(
        msg_data.get("source"), msg_data.get("destination")
    )
    if maybe_group:
        # if we can't notice group membership changes,
        # we could check if the person is still in the group
        logging.info("sending a group")
        group = maybe_group[0].get("group_id")
        # if it's a group, the to/from is already in the group name
        text = msg_data.get("message", "<empty message>")
        await session.send_message(None, text, group=group)
    elif maybe_signal_dest:
        recipient = maybe_signal_dest[0].get("destination")
        # send hashmap as signal message with newlines and tabs and stuff
        keep = ("source", "destination", "message")
        msg_clean = {k: v for k, v in msg_data.items() if k in keep}
        await session.send_message(recipient, msg_clean)
    else:
        logging.info("falling back to admin")
        if not msg_data:
            msg_data["text"] = await request.text()
        recipient = utils.get_secret("ADMIN")
        msg_data[
            "note"
        ] = "fallback, signal destination not found for this sms destination"
        if (agent := request.headers.get("User-Agent")) :
            msg_data["user-agent"] = agent
        # send the admin the full post body, not just the user-friendly part
        await session.send_message(recipient, msg_data)
    return web.Response(text="TY!")


app.add_routes([web.post("/inbound", inbound_sms_handler)])

if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = Forest()

    group_routing_manager = GroupRoutingManager()
    web.run_app(app, port=8080, host="0.0.0.0")
