import logging
from collections import defaultdict
from typing import NamedTuple, NewType, Optional  # , Literal

import mc_util
from forest.core import Message, PayBot, Response, run_bot

# notes: collect phone numbers, preferred name,
# number of tickets
#
#
# blastm
#


# ! Today we're selling a limited number of hand-painted shoes!
# -> https://lh5.googleusercontent.com/ZyXMq3SnkOjvTSbHsMoLCugs_wAU7BKQlLhIWokrAV5XfCVHq3SP4TN8pnnEk1LTqMbkS8-cB6i8zHkEXve9Sa_5uBqWaRlqf2qryjueXPPHJLpHv_QHqtOUHhEBjQsSlA=w640
# entry.1941031617🔜? Which color would you like to order?
#  - Please specify the color, referencing the above image.
#   - (Rehu, Arazan, Ketu, Kaspian, Lapis)

shoe_spec = """entry.2055232012🔜? What size shoes do you wear? (specify M/F as needed)
entry.1000020🔜? What name should we put on your package?
entry.164576404🔜? What is your mailing address?
entry.2032061264🔜$ $0.01 MOB
entry.1000023🔜? Any questions or comments?
https://docs.google.com/forms/d/e/1FAIpQLSdY53W49HhpwZ3g6H_w4GxrnPbVZt-xPvoen-KkhTHp4l72bg/formResponse🔜? confirm
"""

test_spec = """
entry.1097373330🔜? why does what who
entry.2131770336🔜? have you stopped drinking litres of vodka every morning yet?
entry.87194809🔜$ $0.01 MOB
https://docs.google.com/forms/d/e/1FAIpQLSfzlSloyv4w8SmLNR4XSSnSlKJ7WFa0wPMvEJO-5cK-Zb6ZdQ/formResponse🔜? confirm
"""

event_spec = """entry.559352220🔜? What is your name?
entry.1525502581🔜$ $0.01 MOB
https://docs.google.com/forms/d/e/1FAIpQLSeT2crTF86MwpRNH0XKpzV31MNg9pKcL8_LodpM4hb0FA6ujw/formResponse🔜? confirm
"""

Action = NewType("Action", str)  # Literal["!", "?", "$"] # "#"
Prompt = NamedTuple("Prompt", [("qid", str), ("action", Action), ("text", str)])
User = NewType("User", str)

# moneyprompt - metadata, message, value


def load_spec(spec: str) -> list[Prompt]:
    prompts = []
    for line in spec.split("\n"):
        if line:
            # gauranteed safe seperator, no escaping necessary
            qid, stuff = line.split("🔜", 1)
            action, text = stuff.split(" ", 1)
            prompts.append(Prompt(qid, Action(action), text))
    return prompts


class FormBot(PayBot):
    spec: list[Prompt] = load_spec(event_spec)
    issued_prompt_for_user: dict[User, Prompt] = {}
    next_states_for_user: dict[User, list[Prompt]] = defaultdict(list)
    user_data: dict[User, dict[str, str]] = defaultdict(dict)

    """create table if not exists form_messages
    (ts timestamp, source text, message text, question text"""

    async def start_process(self) -> None:
        try:
            address = await self.mobster.get_address()
            logging.info("got address?")
        except IndexError:
            await self.mobster.create_account()
            address = await self.mobster.get_address()
        b64 = mc_util.b58_wrapper_to_b64_public_address(address)
        logging.info("setting profile")
        await self.set_profile_auxin("Eventbot", payment_address=b64)
        await super().start_process()

    async def do_get_spec(self, _: Message) -> str:
        return repr(self.spec)

    async def do_load_spec(self, msg: Message) -> Response:
        self.spec = load_spec(msg.text)
        return "loaded spec, only processing ?"

    async def price(self, prompt: Prompt) -> float:
        return await self.mobster.usd2mob(
            float(prompt.text.removeprefix("$").removesuffix("MOB").strip())
        )  # maybe this could take FormTakingUser?

    async def issue_prompt_text(self, user: User) -> Optional[str]:
        if len(self.next_states_for_user[user]):
            next_prompt = self.next_states_for_user[user].pop(0)
            self.issued_prompt_for_user[user] = next_prompt
            if next_prompt.action == "$":
                mob = await self.price(next_prompt)
                return f"Please pay {mob} MOB"
            if next_prompt.action == "?":
                return next_prompt.text
        return None

    # maybe this could take PromptedUser?
    async def use_prompt_response(self, user: User, resp: str) -> bool:
        logging.info("using response %s", resp)
        if user in self.issued_prompt_for_user:
            prompt = self.issued_prompt_for_user.pop(user)
            logging.info("using prompt %s", prompt)
            if prompt.text == "confirm" and resp.lower() in "yes":
                logging.info("submitting: %s", self.user_data[user])
                logging.info(
                    await self.client_session.post(
                        prompt.qid, data=self.user_data[user]
                    )
                )
                return True
            self.user_data[user][prompt.qid] = resp
            return True
        return False

    async def next_question(self, message: Message) -> Response:
        user = User(message.source)
        if user not in self.next_states_for_user:
            self.next_states_for_user[user] = list(self.spec)
        maybe_current_prompt = self.issued_prompt_for_user.get(user)
        if (
            maybe_current_prompt
            and maybe_current_prompt.action == "$"
            and not message.payment
        ):
            mob = await self.price(maybe_current_prompt)
            return f"Please pay {mob} MOB"
        # validate input somehow
        prompt_used = await self.use_prompt_response(
            user, message.text or message.payment["receipt"]
        )
        if prompt_used:
            ack = f"recorded: {'payment' if message.payment else message.text}"
        else:
            ack = f"{message.text} yourself"
        logging.info(self.next_states_for_user[user])
        maybe_prompt = await self.issue_prompt_text(user)
        if maybe_prompt:
            logging.info(maybe_prompt)
            if maybe_prompt == "confirm":
                return [
                    "thanks for filling out this form. you said:",
                    self.user_data[user],
                    "Submit?",
                ]
            return f"{ack}. {maybe_prompt}"
        return "thanks for filling out this form"

    async def default(self, message: Message) -> Response:
        if not message.text or message.group:
            return None
        return await self.next_question(message)

    # issue: handling
    async def payment_response(self, msg: Message, amount_pmob: int) -> Response:
        del amount_pmob
        pay_prompt = self.issued_prompt_for_user.get(User(msg.source))
        if not pay_prompt or pay_prompt.action != "$":
            return "not sure what that payment was for"
        price = await self.price(pay_prompt)
        diff = await self.get_user_balance(msg.source) - price
        if diff < price * -0.005:
            diff_mob = await self.mobster.usd2mob(abs(diff))
            return f"Another {abs(diff)} USD ({diff_mob} MOB) buy a phone number"
        if diff < price * 0.005:  # tolerate half a percent difference
            resp = f"Thank you for paying! You've overpayed by {diff} USD. Contact an administrator for a refund"
        else:
            resp = "Payment acknowledged"
        await self.mobster.ledger_manager.put_usd_tx(
            msg.source, -int(price * 100), "form payment"
        )
        self.user_data[User(msg.source)][pay_prompt.qid] = msg.payment["receipt"]
        await self.respond(msg, resp)
        return await self.next_question(msg)


if __name__ == "__main__":
    run_bot(FormBot)
