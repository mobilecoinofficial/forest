#!/usr/bin/python3.9
from decimal import Decimal
from typing import Union
import mc_util
from forest.core import Message, PayBot, Response, requires_admin, run_bot


class Echopay(PayBot):
    """A simple Payments Enabled Bot"""

    fee = int(1e12 * 0.0004)  # Mobilecoin transaction fee

    async def start_process(self) -> None:
        """Runs when the bot starts and sets the Profile"""

        await self.set_payment_address()

        return await super().start_process()

    @staticmethod
    def to_mob(amount_picomob: int) -> Decimal:
        """converts amount from pmob to mob"""
        return mc_util.pmob2mob(amount_picomob).quantize(Decimal("1.0000"))

    @staticmethod
    def to_picomob(amount_mob: Union[int, float, Decimal]) -> int:
        """converts amount from mob to picomob"""
        return mc_util.mob2pmob(amount_mob)

    async def set_payment_address(self) -> None:
        """Updates the Bot Signal Profile to have the correct payments address
        as specified by FS_ACCOUNT_NAME"""

        fs_address = await self.mobster.get_my_address()

        # Singal addresses require Base64 encoding, but full service uses Base58.
        # This method handles the conversion
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

        await self.send_payment(
            message.source, amount_picomob, confirm_tx_timeout=10, receipt_message=""
        )

        return f"Sent you a payment for {str(amount_mob)} MOB"

    @requires_admin
    async def do_pay_user(self, message: Message) -> Response:
        """Send payment to user by phone number: `pay_user +15554135555`"""
        amount_mob = 0.001
        amount_picomob = self.to_picomob(amount_mob)

        ## message.arg1 is the first word of the message after the pay_user command

        if not isinstance(message.arg1, str):
            response = (
                "Please specify the User to be paid as a phone number"
                " with country code example: pay_user +15554135555"
            )
            return response

        recipient = message.arg1
        await self.send_payment(
            recipient,
            amount_picomob,
            confirm_tx_timeout=10,
            receipt_message="Here's some money from your friendly Paymebot",
        )
        return f"Sent Payment to {recipient} for {amount_mob} MOB"

    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        """Triggers on Succesful payment, overriden from forest.core"""

        ##amounts are received in picoMob, convert to Mob for readability
        amount_mob = self.to_mob(amount_pmob)

        return f"Thank you for your payment of {str(amount_mob)} MOB"


if __name__ == "__main__":
    run_bot(Echopay)
