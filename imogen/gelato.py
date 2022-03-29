import aiohttp

import requests
from forest import utils
from forest.core import QuestionBot, Message, Response
import logging

logging.getLogger().setLevel("INFO")

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
        "companyName": "Example",
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


def finagle_json(pdfUrl: str) -> dict:
    new_quote = dict(quoteJson)
    quoteJson["products"][0]["pdfUrl"] = pdfUrl
    return quoteJson


session = aiohttp.ClientSession()
# === Send quote request ===
response = requests.request("POST", quoteUrl, data=quoteJson, headers=headers)
quoteData = response.json()

# === Set-up order create request ===
promiseUid = quoteData["production"]["shipments"][0]["promiseUid"]
orderCreateUrl = "https://api.gelato.com/v2/order/create"
orderCreateJson = (
    """{
    "promiseUid": "%s"
}"""
    % promiseUid
)

# === Send order create request ===


class Gelato:
    def req():
        pass

    def order():
        order_json = json.dumps(finagle_json(pdfUrl="https://example.com"))
        response = requests.request(
            "POST", orderCreateUrl, data=orderCreateJson, headers=headers
        )
        print(response.json())


class GelatoBot(QuestionBot):
    async def do_order(self, msg: Message) -> Response:
        addr = await self.ask_address_question("What's your address")
        return f"your address is {addr}"
