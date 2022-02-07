#!/usr/bin/python3.9
import os
import os.path
import asyncio
import json
import glob
import logging
from decimal import Decimal
from typing import Any, Dict
from textwrap import dedent


import aioprocessing
import base58
from aiohttp import web
from prometheus_async import aio
from prometheus_async.aio import time
from prometheus_client import Summary
from google.protobuf import json_format

from amzqr import amzqr
from scan import scan

import mc_util
from forest.core import Message, QuestionBot, Response, app, hide, utils, requires_admin
from forest.pdictng import aPersistDict
from mc_util import mob2pmob, pmob2mob

FEE = int(1e12 * 0.0004)
REQUEST_TIME = Summary("request_processing_seconds", "Time spent processing request")


class MobFriend(QuestionBot):
    no_repay: list[str] = []
    exchanging_gift_code: list[str] = []
    user_images: Dict[str, str] = {}

    def __init__(self) -> None:
        self.notes = aPersistDict("notes")
        super().__init__()

    async def handle_message(self, message: Message) -> Response:
        if message.attachments and len(message.attachments):
            attachment_info = message.attachments[0]
            attachment_path = attachment_info.get("fileName")
            timestamp = attachment_info.get("uploadTimestamp")
            download_success = False
            download_path = "/dev/null"
            for _ in range(6):
                if attachment_path is None:
                    attachment_paths = glob.glob(
                        f"/tmp/unnamed_attachment_{timestamp}.*"
                    )
                    if len(attachment_paths) > 0:
                        attachment_path = attachment_paths.pop()
                        if ".jpeg" in attachment_path:
                            os.rename(
                                attachment_path,
                                attachment_path.replace(".jpeg", ".jpg", 1),
                            )
                            attachment_path = attachment_path.replace(
                                ".jpeg", ".jpg", 1
                            )
                        download_path = self.user_images[
                            message.source
                        ] = f"{attachment_path}"
                else:
                    download_path = self.user_images[
                        message.source
                    ] = f"/tmp/{attachment_path}"
                if not (
                    os.path.exists(download_path)
                    and os.path.getsize(download_path) == attachment_info.get("size", 1)
                ):
                    await asyncio.sleep(4)
                else:
                    download_success = True
                    break
                download_success = False
            contents = (
                scan(self.user_images[message.source]) if download_success else None
            )
            if contents:
                self.user_images.pop(message.source)
                # pylint: disable=unsubscriptable-object
                payload = message.arg1 = contents[-1][1].decode()
                await self.send_message(
                    message.source, f"Found a QR! Contains:\n{payload}"
                )
                # if it's plausibly b58, check it
                if all(char in base58.alphabet.decode() for char in payload):
                    return await self.do_check(message)
                return None
            if not message.arg0:
                return f"OK, saving this template as {download_path} for when you make a QR later!"
        return await super().handle_message(message)

    async def do_add(self, msg: Message) -> Response:
        """Adds a note for other users and the administrators."""
        if not msg.arg1 and not msg.arg1 == "note":
            if not await self.ask_yesno_question(
                msg.source, "Would you like to add a note for future users?"
            ):
                return "Okay! If you ever want to add a note, you can say 'add note'!"
        keyword = await self.ask_freeform_question(
            msg.source, "What keywords for your note?"
        )
        body = await self.ask_freeform_question(msg.source, "What should the note say?")
        blob = dict(
            From=(msg.uuid or "").split("-")[-1], Keywords=keyword, Message=f'"{body}"'
        )
        await self.send_message(msg.source, blob)
        if not await self.ask_yesno_question(msg.source, "Share this with others?"):
            return "Okay, feel free to try again."
        await self.notes.set(keyword, blob)
        return "Saved!"

    async def _actually_build_wait_and_send_qr(
        self, text: str, user_id: str, image_path: Any = None
    ) -> str:
        if not image_path:
            image_path = self.user_images.get(user_id, "template.png")
        if image_path and "." in image_path:
            extension = image_path.split(".")[-1]
        else:
            extension = "png"
        save_name = f"{user_id}_{base58.b58encode(text[:16]).decode()}.{extension}"
        default_params: dict[str, Any] = dict(save_name=save_name, save_dir="/tmp")
        if image_path:
            default_params.update(
                dict(
                    colorized=True,
                    version=1,
                    level="H",
                    contrast=1.0,
                    brightness=1.0,
                    picture=image_path,
                )
            )
        await self.send_message(user_id, "Building your QR code! Please be patient!")
        p = aioprocessing.AioProcess(
            target=amzqr.run, args=(text,), kwargs=default_params
        )
        p.start()  # pylint: disable=no-member
        await p.coro_join()  # pylint: disable=no-member
        await self.send_message(user_id, text, attachments=[f"/tmp/{save_name}"])
        return save_name

    async def do_signalme(self, _: Message) -> Response:
        """signalme
        Returns a link to share the bot with friends!"""
        return f"https://signal.me/#p/{self.bot_number}"

    async def do_clear(self, msg: Message) -> Response:
        """Clears (if relevant) any saved images."""
        if msg.source in self.user_images:
            return f"Will use default instead of {self.user_images.pop(msg.source)} for next QR code."
        return "No images saved."

    async def do_makeqr(self, msg: Message) -> None:
        """
        /makeqr [text]
          or
        /makeqr "Longer Bit Of Text"

        I'll make a QR Code from the provided text!
        If you send me an image first, I'll use it as a template.
        """

        await self._actually_build_wait_and_send_qr(str(msg.arg1), msg.source)
        return None

    async def do_makegift(self, msg: Message) -> Response:
        """
        /makegift
        I'll use your next payment to make a MobileCoin Gift Code that can be redeemed in other wallets.
        Be sure to include an extra 0.0008 MOB to pay the network fees!"""
        if msg.source in self.exchanging_gift_code:
            self.exchanging_gift_code.remove(msg.source)
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that gift code."
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)
        if msg.source not in self.exchanging_gift_code:
            self.exchanging_gift_code.append(msg.source)
        return "Your next Signal Pay transaction will be converted into a MobileCoin Gift Code that can be redeemed in other wallets.\nYou may now send the bot MOB. For help, send the word 'payments'.\nBe sure to include an extra 0.0008MOB to pay the network fees!"

    async def do_tip(self, msg: Message) -> Response:
        """
        /tip
        Records the next payment as a tip, not intended to make a giftcode, or as an accident."""
        if msg.source not in self.no_repay:
            self.no_repay.append(msg.source)
        if msg.source in self.exchanging_gift_code:
            self.exchanging_gift_code.remove(msg.source)
        return "Your next transaction will be a tip, not refunded!\nThank you!\n(/no_tip cancels)"

    @hide
    async def do_no_tip(self, msg: Message) -> Response:
        """Cancels a tip in progress."""
        if msg.source in self.exchanging_gift_code:
            self.exchanging_gift_code.remove(msg.source)
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
            return "Okay, nevermind about that tip."
        return "Couldn't find a tip in process to cancel!"

    @time(REQUEST_TIME)  # type: ignore
    @requires_admin
    async def do_pay(self, msg: Message) -> Response:
        if msg.arg1:
            payment_notif_sent = await self.send_payment(
                msg.source, mob2pmob(Decimal(msg.arg1))
            )
        else:
            payment_notif_sent = await self.send_payment(msg.source, mob2pmob(0.001))
        if payment_notif_sent:
            logging.info(payment_notif_sent)
            delta = (payment_notif_sent.timestamp - msg.timestamp) / 1000
            await self.admin(f"payment delta: {delta}")
            self.auxin_roundtrip_latency.append((msg.timestamp, "payment", delta))
        return None

    @time(REQUEST_TIME)  # type: ignore
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        if msg.source in self.exchanging_gift_code:
            resp_list = await self.build_gift_code(amount_pmob - FEE)
            gift_code = resp_list[1]
            self.exchanging_gift_code.remove(msg.source)
            if msg.source in self.no_repay:
                self.no_repay.remove(msg.source)
            await self._actually_build_wait_and_send_qr(gift_code, msg.source)
            return None
        if msg.source not in self.no_repay:
            payment_notif = await self.send_payment(msg.source, amount_pmob - FEE)
            if not payment_notif:
                return None
            delta = (payment_notif.timestamp - msg.timestamp) / 1000
            self.auxin_roundtrip_latency.append((msg.timestamp, "repayment", delta))
            return None
        if msg.source in self.no_repay:
            self.no_repay.remove(msg.source)
        return f"Received {str(pmob2mob(amount_pmob)).rstrip('0')}MOB. Thank you for the tip!"

    @hide
    async def do_check_balance(self, msg: Message) -> Response:
        """Checks balance of a gift code."""
        if not msg.arg1:
            return "/check_balance [gift code b58]"
        status = await self.mobster.req_(
            "check_gift_code_status", gift_code_b58=msg.arg1
        )
        pmob = int(status.get("result", {}).get("gift_code_value")) - FEE
        if not pmob:
            return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"
        mob_amt = pmob2mob(pmob)
        claimed = status.get("result", {}).get("gift_code_status", "")
        memo = status.get("result", {}).get("gift_code_memo") or "None"
        if "Claimed" in claimed:
            return "This gift code has already been redeemed!"
        await self.send_message(
            msg.uuid,
            f"Gift code can be redeemed for {(mob_amt).quantize(Decimal('1.0000'))}MOB. ({pmob} picoMOB)\nMemo: {memo}",
        )
        if await self.ask_yesno_question(
            msg.uuid, "Would you like to redeem this gift now? (yes/no)"
        ):
            return await self.do_redeem(msg)
        return f'Okay, send "redeem {msg.arg1}" to redeem at any time!'

    async def do_check(self, msg: Message) -> Response:
        """
        /check [base58 code]
        Helps identify a b58 code. If it's a gift code, it will return the balance."""
        if not msg.arg1:
            msg.arg1 = await self.ask_freeform_question(
                msg.source,
                "Provide a MobileCoin request, address, or gift code as b58 and I'll tell you what it does!",
            )
            if msg.arg1.lower() in "stop,exit,quit,no,none":
                return "Okay, nevermind about that"
            return await self.do_check(msg)
        status = await self.mobster.req_("check_b58_type", b58_code=msg.arg1)
        if status and status.get("result", {}).get("b58_type") == "PaymentRequest":
            status["result"]["data"]["type"] = "PaymentRequest"
            status["result"]["data"]["value"] = str(
                pmob2mob(status["result"]["data"]["value"])
            )
            return status.get("result", {}).get("data")
        if status and status.get("result", {}).get("b58_type") == "TransferPayload":
            return await self.do_check_balance(msg)
        return status.get("result") or status.get("error")

    async def do_showdetails(self, msg: Message) -> Response:
        """
        /showdetails [base58 code]
        Returns detailed information about a base58 code."""
        if msg.arg1:
            details = mc_util.b58_wrapper_to_protobuf(msg.arg1 or "")
            if details:
                output = json_format.MessageToDict(details)
                return json.dumps(output, indent=2)
            return "Sorry, the provided code has an invalid checksum."
        return "Please provide a base58 code!"

    async def do_payme(self, msg: Message) -> Response:
        """
        /payme [amount] [memo]

        Creates a payment request (as QR code and b58 code to copy and paste.)
        For example, /payme 1.0 "Pay me a MOB!"
        will create a payment request with
            * the memo "Pay me a MOB!",
            * a 1MOB value,
            * and the address of the requester's Signal account."""
        address = await self.get_address(msg.source)
        if not address:
            return "Unable to retrieve your MobileCoin address!"
        payload = mc_util.printable_pb2.PrintableWrapper()
        address_proto = mc_util.b58_wrapper_to_protobuf(address)
        if address_proto:
            payload.payment_request.public_address.CopyFrom(
                address_proto.public_address
            )
        else:
            return (
                "Sorry, could not parse a valid MobileCoin address from your profile!"
            )
        if msg.tokens and not (
            isinstance(msg.tokens[0], str)
            and len(msg.tokens) > 0
            and isinstance(msg.tokens[0], str)
            and msg.tokens[0].replace(".", "0", 1).isnumeric()
        ):
            return "Sorry, you need to provide a price (in MOB)!"
        if msg.arg1:
            payload.payment_request.value = mob2pmob(float(msg.arg1))
        if msg.tokens and len(msg.tokens) > 1:
            payload.payment_request.memo = " ".join(msg.tokens[1:])
        payment_request_b58 = mc_util.add_checksum_and_b58(payload.SerializeToString())
        await self._actually_build_wait_and_send_qr(payment_request_b58, msg.source)
        await self.send_message(
            msg.source,
            "Your friend can scan this code in the MobileCoin wallet and use it to pay you on Signal.",
        )
        return None

    async def do_paywallet(self, msg: Message) -> Response:
        """
        /paywallet [b58address] [amount] [memo]

        Creates a payment request (as QR code and b58 code to copy and paste.)
        For example, /paywallet [address] 1.0 "Pay me a MOB!"
        will create a payment request with
            * the destination [b58address],
            * a 1MOB value,
            * the memo "Pay me a MOB!"
        """
        address = msg.arg1
        amount = msg.arg2
        memo = msg.arg3 or ""
        if not address or len(address) < 50:
            await self.send_message(
                msg.source,
                "Please provide a b58 address to be used for this payment request!",
            )
        payload = mc_util.printable_pb2.PrintableWrapper()
        address_proto = mc_util.b58_wrapper_to_protobuf(address or "")
        if not address_proto:
            await self.send_message(
                msg.source, "Sorry, could not find a valid address!"
            )
            msg.arg1 = await self.ask_freeform_question(
                msg.source, "What address would you like to use?"
            )
            return await self.do_paywallet(msg)
        payload.payment_request.public_address.CopyFrom(address_proto.public_address)
        if not amount or not amount.replace(".", "0", 1).isnumeric():
            await self.send_message(
                msg.source, "Sorry, you need to provide a price (in MOB)!"
            )
            msg.arg2 = await self.ask_freeform_question(
                msg.source, "What price would you like to use? (in MOB)"
            )
            return await self.do_paywallet(msg)
        payload.payment_request.value = mob2pmob(Decimal(amount))
        payload.payment_request.memo = memo
        payment_request_b58 = mc_util.add_checksum_and_b58(payload.SerializeToString())
        await self._actually_build_wait_and_send_qr(payment_request_b58, msg.source)
        return None

    async def do_redeem(self, msg: Message) -> Response:
        """
        /redeem [base58 gift code]
        Claims a gift code! Redeems a provided code to the bot's wallet and sends the redeemed balance."""
        if not msg.arg1:
            return "/redeem [base58 gift code]"
        check_status = await self.mobster.req_(
            "check_gift_code_status", gift_code_b58=msg.arg1
        )
        amount_pmob = check_status.get("result", {}).get("gift_code_value")
        claimed = check_status.get("result", {}).get("gift_code_status", "")
        status = await self.mobster.req_(
            "claim_gift_code",
            gift_code_b58=msg.arg1,
            account_id=await self.mobster.get_account(),
        )
        if "Claimed" in claimed:
            return "Sorry, that gift code has already been redeemed!"
        if amount_pmob:
            await self.send_payment(
                msg.source, amount_pmob - FEE, "Gift code has been redeemed!"
            )
            amount_mob = pmob2mob(amount_pmob - FEE).quantize(Decimal("1.0000"))
            return f"Claimed a gift code containing {amount_mob}MOB.\nTransaction ID: {status.get('result', {}).get('txo_id')}"
        return f"Sorry, that doesn't look like a valid code.\nDEBUG: {status.get('result')}"

    async def do_make(self, msg: Message) -> Response:
        """Enter a dialog workflow where you can create a payment request, QR code, or gift code."""
        if not msg.arg1:
            maybe_resp = msg.arg1 = await self.ask_freeform_question(
                msg.source,
                "Would you like to make a QR, (payment) request, or gift (code.\nTo proceed, you can reply one of 'qr', 'request', or 'gift'.",
            )
        else:
            maybe_resp = msg.arg1
        if maybe_resp.lower() == "qr":
            maybe_payload = await self.ask_freeform_question(
                msg.source, "What content would you like to include in this QR code?"
            )
            if maybe_payload:
                msg.arg1 = maybe_payload
                return await self.do_makeqr(msg)
        elif maybe_resp.lower() == "request":
            target = await self.ask_freeform_question(
                msg.source,
                "Who should this pay, you or someone else?\nYou can reply 'me' or 'else'.",
            )
            if target.lower() == "me":
                msg.arg1 = await self.get_address(msg.source)
            else:
                msg.arg1 = await self.ask_freeform_question(
                    msg.source, "What MobileCoin address should this request pay?"
                )
            msg.arg2 = await self.ask_freeform_question(
                msg.source, "For how many MOB should this request be made?"
            )
            msg.arg3 = await self.ask_freeform_question(
                msg.source, "What memo would you like to use? ('None' for empty"
            )
            if msg.arg3.lower() == "none":
                msg.arg3 = ""
            _do_paywallet = await self.do_paywallet(msg)
            return "You can copy and paste your payment result here to test it.\nIf you don't like your result, you can try again!"
        elif maybe_resp.lower().startswith("gift"):
            return await self.do_makegift(msg)
        return "I'm sorry, I didn't get that."

    @hide
    async def do_payments(self, _: Message) -> Response:
        helptext = """If you have payments activated, open the conversation on your Signal mobile app, click on the plus (+) sign and choose payment.\n\nIf you don't have Payments activated follow these instructions to activate it.

1. Update Signal app: https://signal.org/install/
2. Open Signal, tap on the icon in the top left for Settings. If you don’t see *Payments*, reboot your phone. It can take a few hours.
3. Tap *Payments* and *Activate Payments*

For more information on Signal Payments visit:

https://support.signal.org/hc/en-us/articles/360057625692-In-app-Payments"""
        return helptext

    # pylint: disable=too-many-branches,too-many-return-statements
    async def default(self, message: Message) -> Response:
        msg, code = message, message.arg0
        if code == "+":
            return await self.do_payments(msg)
        if code == "?":
            code = msg.arg0 = "help"
        elif code == "y":
            return await self.do_yes(msg)
        elif code == "n":
            return await self.do_no(msg)
        elif code == "help" and msg.arg1:
            try:
                doc = getattr(self, f"do_{msg.arg1}").__doc__
                if doc:
                    if hasattr(getattr(self, f"do_{msg.arg1}"), "hide"):
                        raise AttributeError("Pretend this never happened.")
                    return dedent(doc).strip().lstrip("/")
                return f"{msg.arg1} isn't documented, sorry :("
            except AttributeError:
                return f"No such command '{msg.arg1}'"
        if msg.arg0 and msg.arg0.isalnum() and len(msg.arg0) > 100 and not msg.tokens:
            msg.arg1 = msg.full_text
            return await self.do_check(msg)
        if (
            msg.arg0  # if there's a word
            and len(msg.arg0) > 1  # not a character
            and any(
                msg.arg0 in key.lower() for key in await self.notes.keys()
            )  # and it shows up as a keyword for a note
            and "help" not in msg.arg0.lower()  # and it's not 'help'
            and (
                await self.ask_yesno_question(
                    msg.source,
                    f"There are one or more notes matching {msg.arg0}.\n\nWould you like to view them?",
                )
            )
        ):
            # ask for confirmation and then return all notes
            for keywords in self.notes.dict_:
                if msg.arg0 in keywords.lower():
                    await self.send_message(msg.source, await self.notes.get(keywords))
        elif msg.arg0:
            await self.send_message(
                utils.get_secret("ADMIN"), f"{msg.source} says '{msg.full_text}'"
            )
            return "\n\n".join(
                [
                    "Hi, I'm MOBot!",
                    self.documented_commands(),
                    "I can help you accomplish various tasks in the MobileCoin ecosystem, like\n\tmaking and scanning QR codes,\n\tmaking and decoding payment requests, and\n\tmaking and redeeming Gift Codes.\n\nWould you like to 'make' or 'check' something? You can also send a QR code at any time and I'll try and decode it.",
                ]
            )
        return None

    async def do_help(self, msg: Message) -> Response:
        return await self.default(msg)


if __name__ == "__main__":
    app.add_routes([web.get("/metrics", aio.web.server_stats)])

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = MobFriend()

    web.run_app(app, port=8080, host="0.0.0.0", access_log=None)
