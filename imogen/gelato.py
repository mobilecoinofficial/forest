import json
import logging
from forest import utils
from forest.core import Message, run_bot, QuestionBot

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
    async def post_order(self, quote_data: dict) -> None:
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
        return create_response.get("message", "Order submitted")

    async def get_address_dict(self, msg: Message) -> dict:
        addr_data = await self.ask_address_question_(
            msg.source, require_confirmation=True
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

    async def do_fulfillment(self, msg: Message) -> str:
        if not msg.quote:
            return "Quote a url to use this command"
        image = (
            msg.quoted_text.split(" ")[0]
            or "https://mcltajcadcrkywecsigc.supabase.in/storage/v1/object/public/imoges/life_on_a_new_planetc8e3_upsampled.png"
        )
        user = msg.uuid
        # delivery_name = (await self.get_displayname(msg.uuid)).split("_")[0]
        # if not await self.ask_yesno_question(
        #     user,
        #     f"Should we address your package to {delivery_name}?",
        # ):
        delivery_name = await self.ask_freeform_question(
            user, "To what name should we address your package?"
        )
        try:
            delivery = await self.get_address_dict(msg)
        except KeyError:
            return "Sorry, couldn't get that"
        user_email = await self.ask_email_question(
            user, "What's your email?"
        )  # could stub this out with forest email
        # sorry https://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/
        first, last, *unused = delivery_name.split() + ["", ""]
        recipient = delivery | {
            "countryIsoCode": "US",
            "firstName": first,
            "lastName": last,
            "email": user_email,
            "phone": msg.source,
        }
        current_quote_data = {
            "order": {
                "orderReferenceId": f"{{MyOrderId}}",  # maybe user-promptid-date?
                "customerReferenceId": "{{MyCustomerId}}",  # uuid
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
        return await self.post_order(current_quote_data)


if __name__ == "__main__":
    run_bot(GelatoBot)
