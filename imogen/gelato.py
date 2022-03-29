import aiohttp

import requests
from forest import utils

import logging; logging.getLogger().setLevel("INFO")

# === Define headers ===
headers = {
    "Content-Type": "application/json",
    "X-API-KEY": utils.get_secret("GELATO_API_KEY"),
}

# === Set-up quote request ===
quoteUrl = "https://api.gelato.com/v2/quote"
quoteJson = """{
"order": {
    "orderReferenceId": "{{MyOrderId}}",
    "customerReferenceId": "{{MyCustomerId}}",
    "currencyIsoCode": "USD"
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
    "phone": "123456789"
},
"products": [
    {
        "itemReferenceId": "{{MyItemId}}",
        "productUid": "cards_pf_bx_pt_110-lb-cover-uncoated_cl_4-4_hor",
        "pdfUrl": "https://s3-eu-west-1.amazonaws.com/developers.gelato.com/product-examples/test_print_job_BX_4-4_hor_none.pdf",
        "quantity": 100
    }
]
}"""

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
        response = requests.request(
            "POST", orderCreateUrl, data=orderCreateJson, headers=headers
        )
        print(response.json())

        

    