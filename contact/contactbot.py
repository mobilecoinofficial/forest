#!/usr/bin/python3.9
# Copyright (c) 2021 MobileCoin Inc.
# Copyright (c) 2021 The Forest Team
import logging
from functools import wraps
from typing import Callable, Union, cast
import phonenumbers as pn
import teli
from aiohttp import web
from forest_tables import GroupRoutingManager, PaymentsManager, RoutingManager
from forest import utils
from forest.core import Message, PayBot, Response, app, requires_admin


def takes_number(command: Callable) -> Callable:
    @wraps(command)  # keeps original name and docstring for /help
    async def wrapped_command(self: "PayBot", msg: Message) -> str:
        try:
            # todo: parse (123) 456-6789 if it's multiple tokens
            assert msg.arg1
            parsed = pn.parse(msg.arg1, "US")
            assert pn.is_valid_number(parsed)
            target_number = pn.format_number(parsed, pn.PhoneNumberFormat.E164)
            return await command(self, msg, target_number)
        except (pn.phonenumberutil.NumberParseException, AssertionError):
            return (
                f"{msg.arg1} doesn't look a valid number or user. "
                "did you include the country code?"
            )

    return wrapped_command


class Forest(PayBot):
    def __init__(self, *args: str) -> None:
        self.teli = teli.Teli()
        self.payments_manager = PaymentsManager()
        self.routing_manager = RoutingManager()
        self.group_routing_manager = GroupRoutingManager()
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
        Otherwise, use the default Bot do_x method dispatch
        """
        numbers = await self.get_user_numbers(message)
        if numbers and message.group and message.text:
            group = await self.group_routing_manager.get_sms_route_for_group(
                message.group
            )
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
                quoted_list = [
                    line.split(":\t", 1) for line in message.quoted_text.split("\n")
                ]
                can_be_a_dict = cast(list[tuple[str, str]], quoted_list)

                quoted: dict[str, str] = dict(can_be_a_dict)
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
        return await super().handle_message(message)

    async def do_help(self, _: Message) -> Response:
        # TODO: https://github.com/forestcontact/forest-draft/issues/14
        return (
            "Welcome to the Forest.contact Pre-Release!\n"
            "To get started, try /register, or /status! "
            "If you've already registered, try to send a message via /send."
            ""
        )

    @takes_number
    async def do_send(self, message: Message, sms_dest: str) -> Union[str, dict]:
        """Send an SMS message. Usage: /send <destination> <message>"""
        numbers = await self.get_user_numbers(message)
        if not numbers:
            return "You don't have any numbers. Register with /register"
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

    @takes_number
    async def do_mkgroup(self, message: Message, target_number: str) -> str:
        """Create a group for your SMS messages with a given recipient.
        Messages from that recipient will be posted in that group instead of sent to you.
        Messages sent in that group will be sent to that recipient.
        You can add other Signal users; they'll be able to use your number as well
        """
        numbers = await self.get_user_numbers(message)
        if not numbers:
            return "no"
        await self.send_reaction(message, "\N{Busts In Silhouette}")
        group_resp = await self.signal_rpc_request(
            "updateGroup",
            member=[message.source],
            admin=[message.source],
            name=f"SMS with {target_number} via {numbers[0]}",
        )
        await self.group_routing_manager.set_sms_route_for_group(
            teli.teli_format(target_number),
            teli.teli_format(numbers[0]),
            group_resp.group,
        )
        logging.info(
            "created a group route: %s -> %s -> %s",
            target_number,
            numbers[0],
            group_resp.group,
        )
        return "invited you to a group"

    do_query = do_mkgroup
    if not utils.get_secret("GROUPS"):
        del do_mkgroup, do_query

    async def payment_response(self, msg: Message, amount_pmob: int) -> str:
        del amount_pmob
        diff = await self.get_user_balance(msg.source) - self.usd_price
        if diff < 0:
            return f"Please send another {abs(diff)} USD to buy a phone number"
        if diff == 0:
            return "Thank you for paying! You can now buy a phone number with /order <area code>"
        return f"Thank you for paying! You've overpayed by {diff} USD. Contact an administrator for a refund"

    async def do_status(self, message: Message) -> Union[list[str], str]:
        """List numbers if you have them. Usage: /status"""
        numbers: list[str] = [
            registered.get("id")
            for registered in await self.routing_manager.get_id(message.source)
        ]
        if numbers and len(numbers) == 1:
            # registered, one number
            return f'Hi {message.name}! We found {numbers[0]} registered for your user. Try "/send {message.source} Hello from Forest Contact via {numbers[0]}!".'
        # registered, many numbers
        if numbers:
            return f"Hi {message.name}! We found several numbers {numbers} registered for your user. Try '/send {message.source} Hello from Forest Contact via {numbers[0]}!'."
        # paid but not registered
        if await self.get_user_balance(message.source) > 0 and not numbers:
            return [
                "Welcome to the beta! Thank you for your payment. Please contact support to finish setting up your account by requesting to join this group. We will reach out within 12 hours.",
                "https://signal.group/#CjQKINbHvfKoeUx_pPjipkXVspTj5HiTiUjoNQeNgmGvCmDnEhCTYgZZ0puiT-hUG0hUUwlS",
                "Alternatively, try /order <area code>",
            ]
        # not paid, not registered
        return (
            "We don't see any Forest Contact numbers for your account!"
            " If you would like to register a new number, "
            'try "/register" and following the instructions.'
        )

    usd_price = 0.5

    async def do_register(self, message: Message) -> Response:
        """register for a phone number"""
        if int(message.source[1:3]) in (44, 49, 33, 41):
            # keep in sync with https://github.com/signalapp/Signal-Android/blob/master/app/build.gradle#L174
            return "Please send {await self.mobster.usd2mob(self.usd_price)} via Signal Pay"
        mob_price_exact = await self.mobster.create_invoice(
            self.usd_price, message.source, "/register"
        )
        address = await self.mobster.get_address()
        return [
            f"The current price for a SMS number is {mob_price_exact}MOB/month. If you would like to continue, please send exactly...",
            f"{mob_price_exact}",
            "to",
            address,
            "Upon payment, you will be able to select the area code for your new phone number!",
        ]

    async def get_user_balance(self, account: str) -> float:
        res = await self.mobster.ledger_manager.get_usd_balance(account)
        return float(round(res[0].get("balance"), 2))

    async def do_balance(self, message: Message) -> str:
        """Check your balance"""
        balance = await self.get_user_balance(message.source)
        return f"Your balance is {balance} USD"

    async def do_pay(self, message: Message) -> str:
        if message.arg1 == "shibboleth":
            await self.mobster.ledger_manager.put_usd_tx(
                message.source, int(self.usd_price * 100), "shibboleth"
            )
            return "...thank you for your payment. You can buy a phone number with /order <area code>"
        if message.arg1 == "sibboleth":
            return "sending attack drones to your location"
        return "no"

    async def do_order(self, msg: Message) -> str:
        """Usage: /order <area code>"""
        if not (msg.arg1 and len(msg.arg1) == 3 and msg.arg1.isnumeric()):
            return """Usage: /order <area code>"""
        diff = await self.get_user_balance(msg.source) - self.usd_price
        if diff < 0:
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
            await self.mobster.ledger_manager.put_usd_tx(
                msg.source, -int(self.usd_price * 100), number
            )
            return f"You are now the proud owner of {number}"
        return "Database error?"

    @requires_admin
    async def do_make_rule(self, msg: Message) -> Response:
        """creates or updates a routing rule.
        usage: /make_rule <teli number> <signal destination number>"""
        if msg.source != utils.get_secret("ADMIN"):
            return "Sorry, this command is only for admins"
        teli_num, signal_num = msg.text.split(" ")
        _id = teli.teli_format(teli_num)
        destination = utils.signal_format(signal_num)
        if not (_id and destination):
            return "that doesn't look like valid numbers"
        return await self.routing_manager.execute(
            "insert into routing (id, destination, status) "
            f"values ('{_id}', '{destination}', 'assigned') on conflict (id) do update "
            f"set destination='{destination}', status='assigned' "
        )

    if not utils.get_secret("ORDER"):
        del do_order, do_pay

    async def start_process(self) -> None:
        """Make sure full-service has a wallet before starting signal"""
        try:
            await self.mobster.get_address()
        except IndexError:
            await self.mobster.import_account()
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
            if dest := row.get("destination"):
                new_dest = utils.signal_format(dest)
                await self.routing_manager.set_destination(row.get("id"), new_dest)
        await self.datastore.account_interface.migrate()
        await self.group_routing_manager.execute("DROP TABLE IF EXISTS group_routing")
        await self.group_routing_manager.create_table()


async def inbound_sms_handler(request: web.Request) -> web.Response:
    """Handles SMS messages received by our numbers.
    Try groups, then try users, otherwise fall back to an admin
    """
    bot = request.app.get("bot")
    msg_data: dict[str, str] = dict(await request.post())  # type: ignore
    if not bot:
        # no live worker bots
        # if we can't get a signal delivery receipt/bad bot, we could
        # return non-200 and let teli do our retry
        # however, this would require awaiting output from signal; tricky
        await request.app["client_bot"].post(
            "https://counter.pythia.workers.dev/post", data=msg_data
        )
        return web.Response(status=504, text="Sorry, no live workers.")
    sms_destination = msg_data.get("destination")
    # lookup sms recipient to signal recipient
    maybe_signal_dest = await bot.routing_manager.get_destination(sms_destination)
    maybe_group = await bot.group_routing_manager.get_group_id_for_sms_route(
        msg_data.get("source"), msg_data.get("destination")
    )
    if maybe_group:
        # if we can't notice group membership changes,
        # we could check if the person is still in the group
        logging.info("sending a group")
        group = maybe_group[0].get("group_id")
        # if it's a group, the to/from is already in the group name
        text = msg_data.get("message", "<empty message>")
        await bot.send_message(None, text, group=group)
    elif maybe_signal_dest:
        recipient = maybe_signal_dest[0].get("destination")
        # send hashmap as signal message with newlines and tabs and stuff
        keep = ("source", "destination", "message")
        msg_clean = {k: v for k, v in msg_data.items() if k in keep}
        await bot.send_message(recipient, msg_clean)
    else:
        logging.info("falling back to admin")
        if not msg_data:
            msg_data["text"] = await request.text()
        recipient = utils.get_secret("ADMIN")
        msg_data[
            "note"
        ] = "fallback, signal destination not found for this sms destination"
        if agent := request.headers.get("User-Agent"):
            msg_data["user-agent"] = agent
        # send the admin the full post body, not just the user-friendly part
        await bot.send_message(recipient, msg_data)
    return web.Response(text="TY!")


app.add_routes([web.post("/inbound", inbound_sms_handler)])

if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = Forest()
        our_app["routing"] = RoutingManager()
        our_app["group_routing"] = GroupRoutingManager()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
