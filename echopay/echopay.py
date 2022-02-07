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
    
    
    #mobster is a class that helps make api calls to the full service API. We use it for account management
    mobster = Mobster() 

    fee = int(1e12 * 0.0004) #Mobilecoin transaction fee
    
    async def start_process(self) -> None:
        await self.set_payment_address()

        return await super().start_process()

    def to_mob (self,pmob: int) -> Decimal:
        """ converts amount from pmob to mob """
        return mc_util.pmob2mob(pmob).quantize(Decimal('1.0000'))

    def to_pmob (self,mob: Union[int,float,Decimal] ) -> int:
        """ converts amount from mob to pmob """
        return mc_util.mob2pmob(mob)

    async def set_payment_address(self) -> None:
        fs_address= await self.mobster.get_address()
        signal_address= mc_util.b58_wrapper_to_b64_public_address(fs_address)
        await self.set_profile_auxin(
            given_name="PaymeBot",
            payment_address=signal_address,
            profile_path='avatar.png'
        )       
        

    async def do_pay_me(self, message:Message) -> Response:
        """Sends payment to requestee for a certain amount"""
        payment_amount = 0.001 ##payment amount in MOB
        amount_pmob = self.to_pmob(payment_amount)
        await self.send_payment(message.source,amount_pmob)
        return f"sent you a payment for {str(payment_amount)} MOB"


    @requires_admin
    async def do_pay_user(self, message:Message) -> Response:
        payment_amount = 0.001
        amount_pmob = self.to_pmob(payment_amount)

        # if message.arg1
        
        await self.send_payment()
        return "will pay user"
        


    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        """ Triggers on Succesful payment"""

        amount_mob=self.to_mob(amount_pmob)

        return f"Thank you for your payment of {str(amount_mob)} MOB"



if __name__ == "__main__":
    run_bot(Echopay)
