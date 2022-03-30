import aiohttp

import requests
from forest import utils
from forest.core import QuestionBot, Message, Response, run_bot
from forest.talkback import TalkBack
import logging

# === Define headers ===
headers = {
    "Content-Type": "application/json",
    "X-API-KEY": utils.get_secret("GELATO_API_KEY"),
}

# === Set-up quote request ===
quoteUrl = "https://api.gelato.com/v2/quote"
quoteJson = {
    "order": {
        "orderReferenceId": "{{MyOrderId}}",
        "customerReferenceId": "{{MyCustomerId}}",
        "currencyIsoCode": "USD",
    },
    "recipient": {
        "countryIsoCode": "US",
        "firstName": "Paul",
        "lastName": "Smith",
        "addressLine1": "451 Clarkson Ave",
        "addressLine2": "Brooklyn",
        "stateCode": "NY",
        "city": "New York",
        "postcode": "11203",
        "email": "apisupport@gelato.com",
        "phone": "123456789",
    },
    "products": [
        {
            "itemReferenceId": "{{MyItemId}}",
            "productUid": "metallic_200x300-mm-8x12-inch_3-mm_4-0_hor",
            "pdfUrl": "https://s3-eu-west-1.amazonaws.com/developers.gelato.com/product-examples/test_print_job_BX_4-4_hor_none.pdf",
            "quantity": 1,
        }
    ],
}


# def finagle_json(pdfUrl: str) -> dict:
#     new_quote = dict(quoteJson)
#     new_quote["products"][0]["pdfUrl"] = pdfUrl
#     return quoteJson


orderCreateUrl = "https://api.gelato.com/v2/order/create"


class Gelato:
    def __init__(self) -> None:
        self.session = aiohttp.ClientSession()

    async def order(self, quote_data: dict) -> None:
        # === Send quote request ===
        async with self.session.post(quoteUrl, data=quote_data, headers=headers) as r:
            quote_data = await r.json()
        # === Send order create request ===
        async with self.session.post(
            orderCreateUrl,
            data={"promiseUid": quote_data["production"]["shipments"][0]["promiseUid"]},
        ) as r:
            logging.info(await r.json())


class GelatoBot(QuestionBot):
    gelato = Gelato()

    async def do_order(self, msg: Message) -> dict:
        addr_data = await self.ask_address_question_(
            "What's your address", require_confirmation=True
        )
        bits = {
            field: component["long_name"]
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

    async def fulfillment(self, msg: Message) -> None:
        user = msg.uuid
        delivery_name = (await self.get_displayname(msg.uuid)).split("_")[0]
        if not await self.ask_yesno_question(
            user,
            f"Should we address your package to {delivery_name}?",
        ):
            delivery_name = await self.ask_freeform_question(
                user, "To what name should we address your package?"
            )
        delivery = await self.do_order(msg)
        user_email = await self.ask_email_question(
            user, "What's your email?"
        )  # could stub this out with forest email
        # sorry https://www.kalzumeus.com/2010/06/17/falsehoods-programmers-believe-about-names/
        first, last = delivery_name.split() + ["", ""]
        recipient = delivery | {
            "countryIsoCode": "US",
            "firstName": first,
            "lastName": last,
            "email": user_email,
            "phone": msg.source,
        }
        current_quote_data: dict = dict(quoteJson)
        current_quote_data["recipient"] = recipient
        current_quote_data["products"][0][
            "pdfUrl"
        ] = "https://mcltajcadcrkywecsigc.supabase.in/storage/v1/object/public/imoges/life_on_a_new_planetc8e3_upsampled.png"
        await self.gelato.order(current_quote_data)
        # await self.donation_rewards.set
        #     donation_uid,
        #     f'{delivery_name}, "{delivery_address}", {merchandise_size}, {user_email}, {user_phone}',
        # )
        # await self.send_message(user, await self.dialog.get("GOT_IT", "GOT_IT"))
        # return donation_uid


if __name__ == "__main__":
    run_bot(GelatoBot)
