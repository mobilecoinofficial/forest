#!/usr/bin/python3.9
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

fee = int(1e12 * 0.0004)

# REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class Echopay(PayBot):
    
    
    #mobster is a class that helps make api calls to the full service API. We use it for account management
    mobster = Mobster() 

    def to_mob (self,pmob: int) -> Decimal:
        """ converts amount from pmob to mob """
        return mc_util.pmob2mob(pmob).quantize(Decimal('1.0000'))

    def to_pmob (self,mob: Union[int,float,Decimal] ) -> int:
        """ converts amount from mob to pmob """
        return mc_util.mob2pmob(mob)
        

    async def do_payme(self, message:Message) -> Response:
        """Sends payment to requestee for a certain amount"""
        payment_amount = 0.001 ##payment amount in MOB
        
        amount_pmob = self.to_pmob(payment_amount)
        
        password="please"

        if message.arg1 == password:            
            await self.send_payment(message.source,amount_pmob)        
            return f"Of course, here's {str(payment_amount)} MOB"

        elif message.arg1 == None:
            return "What's the password?"

        else: 
            return "That's not the right password!!"




    @requires_admin
    async def do_pay_user(self, message:Message) -> Response:
        return "will pay user"
        


    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        """ Triggers on Succesful payment"""

        amount_mob=self.to_mob(amount_pmob)

        if amount_mob > 0.002:
            return f"Wow! Thank you for your payment of {str(amount_mob)} MOB"
        else:
            return "Thanks I guess"


if __name__ == "__main__":
   run_bot(Echopay)
