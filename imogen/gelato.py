import json
import logging
import time
import asyncio
import mc_util
from forest import utils
from forest.core import Message, run_bot, QuestionBot, Response

# === Define headers ===
headers = {
    "Content-Type": "application/json",
    "X-API-KEY": utils.get_secret("GELATO_API_KEY"),
}
logging.info(headers)

# === Set-up quote request ===
quote_url = "https://api.gelato.com/v2/quote"
order_create_url = "https://api.gelato.com/v2/order/create"


class GelatoBot(QuestionBot):
    price = 10  # MOB Price should be negative amount

    async def post_order(
        self,
        quote_data: dict,
        msg: Message,
    ) -> Response:

        final_confirmation = await self.ask_yesno_question(
            msg.uuid,
            f"I will place your order now and deduct {self.price} MOB. Is this ok? (y/n)",
        )

        if not final_confirmation:
            return await self.cancel_fulfillment(msg)
        balance = mc_util.pmob2mob(await self.get_user_pmob_balance(msg.source))
        if balance < self.price:  # Images go for 10 MOB
            return await self.send_message(
                msg.uuid,
                f"It seems you no longer have enough MOB in your balance to place your order. Make sure you have at least {self.price}MOB in your Imogen Balance to order a print.",
            )
        # === Send quote request ===
        async with self.client_session.post(
            quote_url, data=json.dumps(quote_data), headers=headers
        ) as r:
            quote_response = await r.json()
        logging.info(quote_response)
        # === Send order create request ===
        create_data = {
            "promiseUid": quote_response["production"]["shipments"][0]["promiseUid"]
        }
        async with self.client_session.post(
            order_create_url,
            data=json.dumps(create_data),
            headers=headers,
        ) as r:
            create_response = await r.json()
            logging.info(create_response)
        await self.mobster.ledger_manager.put_pmob_tx(
            msg.source,
            -round(self.price * await self.mobster.get_rate() * 100),
            -mc_util.mob2pmob(self.price),
            f"{msg.source}: {time.time()}",
        )
        return await self.send_message(
            msg.uuid, create_response.get("message", "Order submitted")
        )

    async def get_address_dict(self, msg: Message) -> dict:
        addr_data = await self.ask_address_question_(
            msg.uuid, require_confirmation=True
        )
        if not addr_data:
            return {}
        bits = {
            field: component["short_name"]
            for component in addr_data["address_components"]
            for field in component["types"]
        }
        return {
            "addressLine1": bits["street_number"] + " " + bits["route"],
            "addressLine2": bits["locality"],
            "stateCode": bits["administrative_area_level_1"],
            "city": bits["locality"],
            "postcode": bits["postal_code"],
        }

    async def do_buy(self, msg: Message) -> str:
        """Buy a physical aluminum print of an Imogen Image. Reply to an image with "upsample" to upsample it, then reply to the upsampled image with buy to buy it"""
        if not msg.quote or not msg.quote.startswith("http"):
            return "Quote a url to use this command. Try responding to a generated image with 'upsample'"

        balance = mc_util.pmob2mob(await self.get_user_pmob_balance(msg.source))
        if balance < self.price:  # Images go for 8 MOB
            return "You need 10 MOB of Imogen Balance to buy a print. Send Imogen a payment and try again."

        asyncio.create_task(self.fulfillment(msg))
        if msg.group:
            return "DMing you to complete your transaction"

    async def cancel_fulfillment(self, msg: Message) -> str:
        return await self.send_message(msg.uuid, "Ok, cancelling your oder.")

    async def fulfillment(self, msg: Message) -> Response:
        ## TODO if quoting regular Imoge, upsample it instead and tell user how to order from that.
        # if msg.quoted_text:
        #     self.do_upsample()
        #     return "You need an upsample image to "
        image = msg.quoted_text.split()[0]
        user = msg.uuid
        # delivery_name = (await self.get_displayname(msg.uuid)).split("_")[0]
        # if not await self.ask_yesno_question(
        #     user,
        #     f"Should we address your package to {delivery_name}?",
        # ):
        delivery_name = await self.ask_freeform_question(
            user, "To what name should we address your package?"
        )
        ## TODO refactor cancel flow
        if delivery_name in self.TERMINAL_ANSWERS:
            return await self.cancel_fulfillment(msg)
        try:
            delivery = await self.get_address_dict(msg)
        except KeyError as e:
            logging.info(e)
            return "Sorry, couldn't get that. Cancelling your order."
        if not delivery:
            return await self.cancel_fulfillment(msg)
        user_email = await self.ask_email_question(
            user, "What's your email?"
        )  # could stub this out with forest email
        if user_email is None:
            return await self.cancel_fulfillment(msg)
        # sorry https://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/
        first, last, *unused = delivery_name.split() + ["", ""]
        ## TODO have this account for international users
        recipient = delivery | {
            "countryIsoCode": "US",
            "firstName": first,
            "lastName": last,
            "email": user_email,
            "phone": msg.source,
        }
        order_id = msg.uuid + str(int(time.time()))
        current_quote_data = {
            "order": {
                "orderReferenceId": order_id,
                "customerReferenceId": msg.uuid,
                "currencyIsoCode": "USD",
            },
            "products": [
                {
                    "itemReferenceId": "{{MyItemId}}",  # maybe prompt id
                    "productUid": "metallic_200x300-mm-8x12-inch_3-mm_4-0_hor",
                    "pdfUrl": image,
                    "quantity": 1,
                }
            ],
            "recipient": recipient,
        }
        logging.info(current_quote_data)
        resp = await self.post_order(current_quote_data, msg)
        if resp == "Promise Uid is accepted for processing":
            return f"Order is being processed. Your order id {order_id}"
        return f"Something went wrong: {resp}"


if __name__ == "__main__":
    run_bot(GelatoBot)
