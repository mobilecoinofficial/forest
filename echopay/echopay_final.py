#!/usr/bin/python3.9
from audioop import add
from inspect import modulesbyfile
import logging
from unicodedata import decimal
from urllib import response

from aiohttp import web
from forest.core import Message, PayBot, Response, app, requires_admin, run_bot
from forest.utils import get_secret
from decimal import Decimal
import mc_util
from typing import Union
from forest.payments_monitor import Mobster


class Echopay(PayBot):

    # mobster is a class that helps make api calls to the full service API. We use it for account management
    mobster = Mobster()

    fee = int(1e12 * 0.0004)  # Mobilecoin transaction fee

    async def start_process(self) -> None:
        """Runs when the bot starts and sets the Profile"""

        await self.set_payment_address()

        return await super().start_process()

    def to_mob(self, amount_picomob: int) -> Decimal:
        """converts amount from pmob to mob"""
        return mc_util.pmob2mob(amount_picomob).quantize(Decimal("1.0000"))

    def to_picomob(self, amount_mob: Union[int, float, Decimal]) -> int:
        """converts amount from mob to pmob"""
        return mc_util.mob2pmob(amount_mob)

    async def set_payment_address(self) -> None:
        """Updates the Bot Signal Profile to have the correct payments address as specified by FS_ACCOUNT_NAME"""
        fs_address = await self.mobster.get_my_address()

        ##Singal addresses require Base64 encoding, but full service uses Base58. This method handles the conversion
        signal_address = mc_util.b58_wrapper_to_b64_public_address(fs_address)

        await self.set_profile_auxin(
            given_name="PaymeBot",
            family_name="",
            payment_address=signal_address,
            profile_path="avatar.png",
        )

    async def do_payme(self, message: Message) -> Response:
        """Sends payment to requestee for a certain amount"""
        amount_mob = 0.001  ##payment amount in MOB
        amount_picomob = self.to_picomob(amount_mob)

        password = "please"

        if message.arg1 == password:
            await self.send_payment(message.source, amount_picomob)
            return f"Of course, here's {str(amount_mob)} MOB"

        elif message.arg1 == None:
            return "What's the secret word?"

        else:
            return "That's not the right secret word!!"

    @requires_admin
    async def do_pay_user(self, message: Message) -> Response:
        """Send payment to user by phone number: `pay_user +15554135555`"""
        amount_mob = 0.001
        amount_picomob = self.to_picomob(amount_mob)
        ## message.arg1 is the first word of the message after the pay_user command
        recipient = message.arg1

        await self.send_payment(
            recipient,
            amount_picomob,
            confirm_tx_timeout=10,
            receipt_message="Here's some money from your friendly Paymebot",
        )
        return f"Sent Payment to {recipient} for {amount_mob} MOB"

    async def payment_response(self, message: Message, amount_picomob: int) -> Response:
        """Triggers on Succesful payment"""

        amount_mob = self.to_mob(
            amount_picomob
        )  ##amounts are received in picoMob, convert to Mob for readability

        if amount_mob > 0.002:
            return f"Wow! Thank you for your payment of {str(amount_mob)} MOB"
        else:
            return "Thanks I guess"


if __name__ == "__main__":
    run_bot(Echopay)
