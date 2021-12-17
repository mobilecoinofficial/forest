#!/usr/bin/python3.9
"""Executable file which starts up a bot which does mobilecoin airdrops"""
import logging
from enum import Enum
from typing import Any, Union
from dataclasses import dataclass, field
from asyncio import create_task
from aiohttp import web
from forest.core import PayBot, app, Message, Response, requires_admin, hide
from forest.utils import get_secret


def try_cast_float(value: Any) -> Union[float, Any]:
    """
    Attempt to cast to float
    """
    if isinstance(value, bool):
        logging.warning(
            "bool value passed, aborting typecast to avoid undesired casting errors"
        )
        return value
    try:
        fvalue = float(value)
        return fvalue
    except Exception:  # pylint: disable=broad-except
        return value


def try_cast_int(value: Any) -> Union[int, Any]:
    """
    Attempt to cast to int
    """
    if isinstance(value, (float, bool)):
        logging.warning(
            "float or bool value passed, aborting typecast to avoid undesired casting errors"
        )
        return value
    try:
        ivalue = int(value)
        return ivalue
    except Exception:  # pylint: disable=broad-except
        return value


class States(Enum):
    """
    Possible environment states. States are not mutually exclusive
    """

    SETUP = 1
    NEEDS_FUNDING = 2
    READY_TO_LAUNCH = 3
    LIVE = 4
    AIRDROP_FULL = 5
    AIRDROP_FINISHED = 6
    NO_AIRDROP = 7
    ERROR = 8


@dataclass
class Airdrop:
    """
    Base aidrop configuration type
    """

    # pylint: disable=R0201
    def is_configured_correctly(self) -> bool:
        """
        Is the airdrop configured correctly, alwayas false for base aidrop type
        """
        return False


@dataclass
class SimpleAirdrop(Airdrop):
    """
    Configuration meant to represent an airdrop that distributes an equal
    amount to each participant
    """

    entry_price: float = -1.0
    drop_amount: float = -1.0
    max_entrants: int = -1
    start_block: int = -1
    in_setup_dialog: bool = False
    setup_script: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        logging.info("Instantiating airdrop configuration, type: simple")
        self.setup_script = dict(
            welcome="Welcome to aidrop setup, I will now ask series of questions to setup"
            "your drop \n You may exit this at anytime by typing another /command"
            "or by typing 'exit' at anytime",
            entry_price="What is the entry price for this airdrop (enter integer or decimal)",
            drop_amount="How many mob will be given out to each entrant (enter integer or decimal)",
            max_entrants="How many entrants total are allowed? (enter integer or decimal)",
            setup_finished="Airdrop setup complete! Type /launch_aidrop to begin the airdrop",
            exit="Exiting setup. You may return to this dialog anytime by typing /setup_airdrop",
        )

    def is_configured_correctly(self) -> bool:
        """
        Checks for valid values for Simple Airdrop config type

        Returns:
          bool: whether setup for airdrop is complete
        """
        return self.entry_price > 0 and self.drop_amount > 0 and self.max_entrants > 1

    def get_next_setup_dialog(self) -> str:
        """
        Gets next dialog in setup sequence based on current configuration

        Returns:
          str: next setup dialog
        """
        if self.entry_price < 0:
            return self.setup_script.get(
                "entry_price", "What is the entry price for this airdrop?"
            )
        if self.drop_amount < 0:
            return self.setup_script.get(
                "drop_amount", "How many MOB will be given out to each entrant?"
            )
        if self.max_entrants < 1:
            return self.setup_script.get(
                "max_entrants", "How many entrants total are allowed?"
            )
        return self.setup_script.get(
            "setup_finished",
            "Aidrop successfully configured, type /launch to begin airdrop",
        )

    def take_next_setup_input(self, value: Any) -> str: # pylint: disable=too-many-return-statements
        """
        Take input via a dialog flow

        args:
          value (Any): Value input during dialog flow

        Returns:
          str: reply string
        """
        if value == "exit":
            self.in_setup_dialog = False
            return self.setup_script.get("exit", "Exiting setup")
        if self.entry_price < 0:
            value = try_cast_float(value)
            if isinstance(value, (float, int)) and (value > 0):
                self.entry_price = value
                return self.get_next_setup_dialog()
            return "Entry price must be a positive number, please re-enter"
        if self.drop_amount < 0:
            value = try_cast_float(value)
            if isinstance(value, (float, int)) and (value > 0):
                self.drop_amount = value
                return self.get_next_setup_dialog()
            return "Drop amount must be a positive number, please re-enter"
        if self.max_entrants < 1:
            value = try_cast_int(value)
            if isinstance(value, (int)) and (value > 0):
                self.max_entrants = value
                return self.get_next_setup_dialog()
            return "Max entrants must be a positive whole number, please re-enter"
        self.in_setup_dialog = False
        return self.get_next_setup_dialog()

    def get_total_airdrop_amount(self, include_fees: bool = True) -> float:
        """
        Calculate total aidrop pot

        args:
          include_fees (bool): return amount plus estimated fees

        Returns
          float: total mob to distribute in airdrop
        """
        total_drop = self.max_entrants * self.drop_amount
        if include_fees:
            return (
                total_drop
                + self.max_entrants * 0.01
                + self.entry_price * self.max_entrants
            )
        return total_drop

    def is_ready_to_launch(self, balance: Union[float, int]) -> bool:
        """
        Check balance to determine if airdrop is ready to start

        args:
          balance (Union[float, int]): wallet balance in pmob

        Returns:
          bool: whether airdrop can start
        """

        cost = self.get_total_airdrop_amount(include_fees=True)
        enough_mob_available = balance < cost
        configured_correctly = self.is_configured_correctly()
        can_start = enough_mob_available and configured_correctly
        if not enough_mob_available:
            logging.warning(
                "Airdrop will cost %s but wallet only has %s", cost, balance
            )
        if not configured_correctly:
            logging.warning("Airdrop is not configured correctly")
        if can_start:
            logging.info(
                "Airdrop configured correctly with sufficient balance, airdrop can start"
            )
        return can_start

    def __repr__(self) -> str:
        resp = (
            "\nAidrop Configuration:\n"
            f"Start Block: {self.start_block}\n"
            f"Drop Amount: {self.drop_amount}\n"
            f"Max Entrants: {self.max_entrants}\n"
            f"Entry Price: {self.entry_price}"
        )
        if self.is_configured_correctly():
            total = self.get_total_airdrop_amount()
            resp += f"\nDrop Total + Fees: {total}"
        return resp


InteractiveAirdrop = Union[SimpleAirdrop]


class AirDropBot(PayBot):
    """
    Bot which takes airdrop entrants and provides a drop!
    """

    def __init__(self) -> None:
        self.name = "MobDripper"
        self.config: Airdrop = Airdrop()
        self.entrant_list: dict = {}
        self.airdrop_finished = False
        super().__init__()

    @staticmethod
    def is_admin(msg: Message) -> bool:
        """
        Determine if message sender is admin
        """
        admin = get_secret("ADMIN")
        return msg.source == admin

    async def get_state(self) -> set[States]:
        """
        get airdrop state based on environment
        """
        states = set()
        conf = self.config
        num_entrants = len(self.entrant_list)
        if isinstance(conf, SimpleAirdrop):
            if self.airdrop_finished:
                states.add(States.AIRDROP_FINISHED)
                return states
            if not conf.is_configured_correctly():
                states.add(States.SETUP)
            else:
                wallet_balance = await self.mobster.get_wallet_balance()
                if conf.is_ready_to_launch(wallet_balance):
                    if conf.start_block > 0:
                        states.add(States.LIVE)
                    else:
                        if num_entrants > 0:
                            states.add(States.ERROR)
                            return states
                        states.add(States.SETUP)
                        states.add(States.READY_TO_LAUNCH)
                else:
                    if num_entrants > 0 or conf.start_block > 0:
                        states.add(States.LIVE)
                        states.add(States.NEEDS_FUNDING)
                        states.add(States.ERROR)
                        logging.critical(
                            "Airdrop is live without funding, please fund wallet immediately!!"
                        )
                    else:
                        states.add(States.SETUP)
                        states.add(States.NEEDS_FUNDING)
                if num_entrants > conf.max_entrants:
                    states.add(States.AIRDROP_FULL)
        else:
            states.add(States.NO_AIRDROP)
        return states

    async def handle_message(self, message: Message) -> Response:
        if self.is_admin(message) and isinstance(self.config, InteractiveAirdrop):
            if self.config.in_setup_dialog:
                if message.command:
                    self.config.in_setup_dialog = False #pylint: disable=attribute-defined-outside-init
                    return await super().handle_message(message)
                return self.config.take_next_setup_input(message.text)
        if message.payment:
            return await self.process_payment(message)
        return await super().handle_message(message)

    async def process_payment(self, message: Message) -> Response:
        """
        Handle refunding of payments to users legitimately entering airdrop or
        sending unsolicted payments
        """
        state = await self.get_state()
        #URGENT: Add submit-transaction comment ability into core
        refund_without_fees = "we are refunding your payment minus transaction fees!"
        #refund_with_fees = "we are refunding your payment and transaction fees!"
        if not States.LIVE in state:
            create_task(self.return_payment(message, "Not Live"))
            return f"No airdrop in progress, {refund_without_fees}"
        if States.AIRDROP_FULL in state:
            create_task(self.return_payment(message, "Full"))
            return f"This airdrop has reached the maximum amount of entrants, {refund_without_fees}"
        if message.source in self.entrant_list and isinstance(self.config,
                SimpleAirdrop):
            create_task(self.return_payment(message, "Already Entered"))
            return f"You've already entered this airdrop, {refund_without_fees}"
        if isinstance(self.config, SimpleAirdrop) and not (message.source in
                self.entrant_list):
            #URGENT: ensure transactions get annotated in case of bot failure
            pass
        return "TODO"

    async def return_payment(self, msg: Message, reason: str) -> None:
        """
        Return payments to the bot

        args:
          msg (Response): Message object sent to the bot
          reason (str): Reason payment is being sent back
        """
        assert msg.payment
        logging.info(msg.payment)
        amount_pmob = await self.mobster.get_receipt_amount_pmob(
                msg.payment["receipt"]
        )
        if isinstance(amount_pmob, int):
            if reason in ("Not Live", "Full", "Already Entered"):
                await self.send_payment(msg.source, amount_pmob, "Your payment has been refunded!")

    async def default(self, message: Message) -> Response:
        conf = self.config
        state = await self.get_state()
        resp = f"You've messaged {self.name}. \n\n"
        if message.txt and not (message.group or message.txt == resp):
            if not isinstance(conf, InteractiveAirdrop) or (States.SETUP in state):
                if self.is_admin(message):
                    resp = "Hi admin, no aidrop is currently configured\n"
                    if States.SETUP in state:
                        resp = "Hi admin, an airdrop configuration is in progress\n"
                    resp += "type /setup_airdrop to enter airdrop setup\n"
                    resp += "\nOther commands:\n" + self.documented_commands()
                    return resp
                resp += "No aidrop is in progress, please check back later"
                return resp

            if States.LIVE in state:
                if self.is_admin(message):
                    resp = "Hello admin, an airdop is in progress\n"
                    if States.NEEDS_FUNDING in state:
                        resp += "but has insufficient funding, PLEASE FUND IMMEDIATELY!"
                        return resp
                    if States.AIRDROP_FULL in state:
                        resp += "Airdrop is full!\n"
                    resp += "Make the drop to current entrants by typing /make_drop\n"
                    resp += "Get airdrop stats with /drop_stats\n"
                    resp += "Cancel the drop with /cancel_drop\n"
                    resp += "\nOther commands:\n" + self.documented_commands()
                    return resp

                resp += "Hello! An aidrop is in progress, "
                if message.source in self.entrant_list:
                    resp += "you've already entered the Airdrop! You'll receive your MoB soon."
                else:
                    resp += (
                        f"{self.name} is currently dropping {conf.drop_amount} to "
                        f"each entrant for the first {conf.max_entrants} "
                        f"entrants. To enter please send {conf.entry_price} "
                        "MOB to this bot with signal pay. You'll be sent "
                        f"back {conf.entry_price} + network fees immediately. "
                        "When the airdrop starts you'll be sent "
                        f"{conf.drop_amount} MOB!"
                    )
                return resp
        return None

    @hide
    @requires_admin
    async def do_drop_stats(self, msg: Message) -> Response: #pylint: disable=unused-argument
        """
        Get stats on airdrop
        """
        state = await self.get_state()
        if States.NO_AIRDROP in state:
            return "Sorry, no aidrop in progress"
        conf = self.config
        num_entrants = len(self.entrant_list)
        wallet_balance = await self.mobster.get_wallet_balance()
        assert isinstance(conf, InteractiveAirdrop)
        if wallet_balance >= 0:
            finances = f"Wallet Balance: {wallet_balance}"
        else:
            finances = "Wallet Balance: Error getting balance!"
        resp = f"{finances}\n" + f"{repr(conf)}"
        if States.LIVE in state:
            prefix = "Airdrop live\n" + f"# of entrants: {num_entrants}\n"
            if States.NEEDS_FUNDING in state:
                prefix += "CRITICAL: AIRDROP HAS ENTRANTS BUT LACKS FUNDING, FUND IMMEDIATELY\n"
            if States.AIRDROP_FULL in state:
                prefix += "AIRDROP FULL!\n"
            return prefix + resp
        if States.SETUP in state:
            setup_done = conf.is_configured_correctly()
            prefix = (
                "Setup is currently in progress\n"
                f"Setup Complete: {setup_done}\n"
                f"Fully Funded: {States.NEEDS_FUNDING in state}\n"
                f"Able to Launch: {States.READY_TO_LAUNCH in state}\n"
            )
            return prefix + resp
        return "Drop in Error State\n" + f"# of entrants {num_entrants}\n" + resp

    @hide
    @requires_admin
    async def do_launch_airdrop(self, msg: Message) -> Response: #pylint: disable=unused-argument
        """
        Put airdrop in state to accept funds from users
        """
        resp = "No airdop configured, cannot launch"
        conf = self.config
        state = await self.get_state()
        if States.LIVE in state:
            return "Airdrop already in progress, cannot launch new aidrop"

        if isinstance(conf, InteractiveAirdrop) and States.SETUP in state:
            if States.READY_TO_LAUNCH in state:
                block_height = await self.mobster.get_current_network_block()
                if block_height < 0:
                    return "Network block height couldn't be found, aborting launch"
                conf.start_block = block_height #pylint: disable=attribute-defined-outside-init
                return f"Airdrop launched, {self.name} will now accept payments"
            if States.NEEDS_FUNDING in state:
                funds = await self.mobster.get_wallet_balance()
                deficit = conf.get_total_airdrop_amount() - funds
                return f"Airdrop needs funding, please fund with {deficit} MoB"
            return "Airdop setup not complete, type /setup_airdop to finish setup"

        if States.ERROR in state:
            resp = "Aidrop is in an error state, please fix before launching"

        return resp

    @hide
    @requires_admin
    async def do_setup_airdrop(self, msg: Message) -> str: #pylint: disable=unused-argument
        """
        Setup new airdrop or re-enter in progress airdrop
        """
        state = await self.get_state()
        if States.NO_AIRDROP in state:
            self.config = SimpleAirdrop()
            assert isinstance(self.config, SimpleAirdrop)
            resp = self.config.setup_script.get("welcome", "Welcome to airdrop setup")
            resp += "\n" + self.config.get_next_setup_dialog()
            self.config.in_setup_dialog = True #pylint: disable=attribute-defined-outside-init
            return resp
        if States.LIVE in state:
            return "An airdrop is in progress, cannot setup a new one"
        if States.ERROR in state:
            return "Airdrop is in error state, cannot enter setup"
        if States.SETUP in state:
            assert isinstance(self.config, InteractiveAirdrop)
            self.config.in_setup_dialog = True #pylint: disable=attribute-defined-outside-init
            return self.config.get_next_setup_dialog()
        return "Unknown Error, cannot setup airdrop"

if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        """
        Start App
        """

        out_app["bot"] = AirDropBot()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
