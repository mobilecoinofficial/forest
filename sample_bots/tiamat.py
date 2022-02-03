#!/usr/bin/python3.9
import asyncio
import logging
import re
import time
from asyncio import Queue, Task, create_task, wait_for
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Union

from aiohttp import web

import mc_util
from forest.core import JSON, Message, PayBot, Response, app
from forest.utils import get_secret

new_line: str = "\n"


@dataclass
class PaymentReceipt:
    """
    Result of an individual payment

    Attributes:
      sender (str): signal account that sent payment
      recipient (str): signal account that received payment
      signal_timestamp (Optional[float]): time of payment notification received from Signal
      amount (Optional[float]): amount in pmob sent, Mobilecoin blockchain must
      be queiried for this amount
      note (Optional[str]): note sent with payment
      confirmation_timestamp Optional[float]: time of receipt confirmation on
      the mobilecoin blockchain
      timeout (bool): flag determining if payment timeout
      timeout_timestamp (Optional[float]): timeout timestamp
    """

    sender: str
    recipient: str
    signal_timestamp: Optional[float] = None
    amount: Optional[float] = None
    note: Optional[str] = None
    confirmation_timestamp: Optional[float] = None
    timeout: bool = False
    timeout_timestamp: Optional[float] = None

    def __eq__(self, other: Any) -> bool:
        # Measure equality based on amount, sender, recipient and note
        if isinstance(other, PaymentReceipt):
            return (
                (self.amount == other.amount)
                and (self.sender == other.sender)
                and (self.recipient == other.recipient)
                and (self.note == other.note)
            )
        return False

    def __repr__(self) -> str:
        msg = (
            f"{new_line}"
            f"Amount: {self.amount}{new_line}"
            f"Note: {self.note}{new_line}"
        )
        if self.timeout:
            return msg + "Timeout before txo confirmation"
        if isinstance(self.confirmation_timestamp, float):
            assert self.signal_timestamp
            msg = (
                msg
                + f"Txo Confirmation Delta: {round(self.confirmation_timestamp - self.signal_timestamp,2)}{new_line}"
            )

        return msg


@dataclass
class TestMessage:
    """
    Represents a message object to or from a bot that
    roughly models the data fields signal messaging clients
    expect

    Attributes:
      recipient (str): recipient of the message
      message (Optional[str]): text content of the message
      group (Optional[str]): target group of the TestMessage
      endsession (bool): send command to reset session/keystate
      attachments (Optional[list[str]]): attachment list
      content (str): used for payments
      sender (Optional[str]): sender of the message
      payment (Optional[tuple[str, Optional[int]]]): payment recipient and
      amount of Mobilecoin to send to recipient
    """

    recipient: str
    message: Optional[str] = None
    group: Optional[str] = None
    endsession: bool = False
    attachments: Optional[Union[list[dict[str, str]], list[str]]] = None
    content: str = ""
    sender: Optional[str] = None
    payment: Optional[tuple[str, Optional[int]]] = None


@dataclass
class TestStep:
    """
    Configuration for an individual test message

    Attributes:
      uid (str): unique identifier for step
      description (str): step description
      message (str): message to send to bot being tested
      expected_response (Optional[TestMessage]): expected response from the bot
      being tested
      expected_receipt (Optional[PaymentReceipt]): expected payment in response
      to message from bot being tested
      delay (float): number of second to wait before executing TestStep
    """

    uid: str
    description: str
    message: TestMessage
    expected_response: Optional[TestMessage] = None
    expected_receipt: Optional[PaymentReceipt] = None
    delay: float = 3.0


@dataclass
class Test:
    """
    Configuration for a multi-message test


    Attributes:
        name (str): unique name for the test
        description (str): description of the test
        recipient (str): signal formatted number
        steps (list[TestStep]): List of test step configurations
        order (str): Order in which to execute test steps
        timeout (float): Maximum time test is allowed to run
        step_timeout (float): Maximum time to wait for replies to sent messages
        payment_timeout (float): Maximum time to wait for Mobilecoin receipts
        validate_payments (bool): Require payments be confirmed on
        the MobileCoin blockchain for test to pass
        payment_validation_strategy (str): Strategy to validate payments. Since
        payments may not be confirmed in order, payments may be validated
        by "amount" which will strictly match payment amounts, by
        "notification_order" which will confirm payments based on order of signal
        notifications, or "confirmation_order" which will validate tests based
        on order of their confirmation on the Mobilecoin blockchain
    """

    name: str
    description: str
    recipient: str
    steps: list[TestStep]
    order: str = "sequential"
    validate_responses: bool = True
    timeout: float = 360.0
    step_timeout: float = 20.0
    payment_timeout: float = 90.0
    validate_payments: bool = True
    payment_validation_strategy: str = "amount"

    def __post_init__(self) -> None:
        if self.order not in ("sequential", "paralllel"):
            raise ValueError("Order must be either sequential or parallel")
        if self.payment_validation_strategy not in (
            "amount",
            "notification_order",
            "confirmation_order",
        ):
            raise ValueError(
                "Payments must be validated by amount, notification_order, or confirmation_order"
            )
        self.validate_self()

    def has_payments(self) -> bool:
        """
        Determine if payments are within test definition

        Returns:
          bool: boolean representing existence of payments within test steps

        """
        payment_info = self.validate_payment_tests()
        if payment_info.get("has_payments"):
            return True
        return False

    def validate_payment_tests(self) -> dict[str, bool]:
        """
        Validates all necessary conditions for payment steps to be valid

        Returns:
          dict[str, bool]: dictionary of test attributes related to payment

        Raises:
          ValueError: if test payments configured incorrectly
        """
        has_payments = False

        for step in self.steps:
            payment = step.message.payment
            if payment:
                has_payments = True

                if not (
                    isinstance(payment, tuple)
                    and isinstance(payment[0], str)
                    and isinstance(payment[1], int)
                ):
                    raise ValueError(
                        (
                            "Payment must be a tuple(recipient(str),amount(int)),"
                            " please check your test step initialization for errors"
                        )
                    )
        return {"has_payments": has_payments}

    def validate_self(self) -> None:
        """
        Ensure test configuration is valid

        Raises:
          ValueError: if test configuration is invalid
        """
        payment_info = self.validate_payment_tests()
        logging.info(f"test is valid, test config: {payment_info}")


@dataclass
class StepResult:
    """
    Data structure representing a text or attachment reply to an individual
    message

    Attributes:
      uid (str): unique id of test step
      message_sent (Optional[TestMessage]): Message sent to bot being tested
      expected_reponse (Optional[TestMessage]): Expected message in response to
      message sent to bot
      actual_response (Optional[TestMessage]): Actual message received from bot
      result (Optional[str]): pass/fail status of step, should take on "pass"
      result if the expected response matches the actual response and "fail"
      otherwise
      python_timestamp (Optional[float]): time of message sent to auxin from Tiamat
      auxin_timestamp (Optional[float]): time auxin sends message to signal server
      send_delay (Optional[float]): Delay between python request to & auxin
      send confirmation
      response_timestamp (Optional[float]): time of message response
      roundtrip_delta (Optional[float]): total roundtrip time of message
    """

    uid: Optional[str] = None
    message_sent: Optional[TestMessage] = None
    expected_response: Optional[TestMessage] = None
    actual_response: Optional[TestMessage] = None
    result: Optional[str] = None
    python_timestamp: Optional[float] = None
    auxin_timestamp: Optional[float] = None
    auxin_roundtrip_latency: Optional[float] = None
    send_delay: Optional[float] = None
    response_timestamp: Optional[float] = None
    roundtrip_delta: Optional[float] = None

    def __repr__(self) -> str:
        expected = self.expected_response.message if self.expected_response else "None"
        got = self.actual_response.message if self.actual_response else "None"
        return f"<expected: '{expected}'; got '{got}'>"


@dataclass
class TestResult:
    """
    Container holding data of the result of a multi-step test.

    Attributes:
      test (Test): test definition that was used
      name (Optional[str]): name of test defintion
      test_account (str): signal account used to run the test
      step_results (list[StepResult]): list of StepResult objects containing
      data on results of individual steps
      payment_receipts (list[PaymentReceipt]): list of payment receipts
      received during test
      expected_receipts (list[PaymentReceipt]): list of receipts expected to be
      received during test
      result (str): pass/fail result on test, pass if all payments and messages
      match expected results, fail otherwise
      start_time (float): start time of test, -1 indicates value not recorded
      end_time (float): time of test completion or error, -1 indicates value
      not recorded
      runtime (float): elapsed time between start_time and endtime
    """

    test: Test = field(repr=False)
    name: Optional[str] = None
    test_account: str = "tester"
    step_results: list[StepResult] = field(default_factory=list, repr=False)
    payment_receipts: list[PaymentReceipt] = field(default_factory=list)
    expected_receipts: list[tuple[TestStep, PaymentReceipt]] = field(
        default_factory=list
    )
    result: str = "pre_initialization"
    start_time: float = -1.0
    end_time: float = -1.0
    runtime: float = -1.0

    def __repr__(self) -> str:
        msg = (
            f"Test: {self.test.name}{new_line}"
            f"Result: {self.result}{new_line}"
            f"Payments:{new_line}"
            f"expected receipts:{new_line}"
            f"{[receipt[1] for receipt in self.expected_receipts]}{new_line}"
            f"actual receipts:{new_line}"
            f"{self.payment_receipts}{new_line}"
            f"Runtime: {round(self.runtime, 2)} seconds"
        )

        return msg

    def __post_init__(self) -> None:
        self.name = self.test.name
        for step in self.test.steps:
            if isinstance(step.expected_receipt, PaymentReceipt):
                self.expected_receipts.append((step, step.expected_receipt))
        if self.test_account != "tester" and isinstance(self.test_account, str):
            self.set_recipient(self.test_account)

    def set_recipient(self, number: str) -> None:
        """
        Sets recipient for TestResult object + any expected receipts

        Args:
          number (str): Number of the bot performing the test
        """
        logging.info(
            f"Setting payment recipient as test orchestration account: {number}"
        )
        self.test_account = number
        if self.expected_receipts:
            for pair in self.expected_receipts:
                receipt = pair[1]
                receipt.recipient = number

    def all_receipts_confirmed(self) -> bool:
        """
        Determine if all receipts were confirmed on Mobilecoin blockchain

        Returns:
          bool: boolean determining if all payments were confirmed
        """

        receipts = self.payment_receipts
        result = [bool(receipt.confirmation_timestamp) for receipt in receipts]
        if result:
            return all(result)
        return False

    def receipts_match(self, strategy: str) -> bool:
        """
        Determine if payment receipts match expected receipts along 3 possible
        strategies ("amount", "notification_order", "confirmation_order")

        Args:
          strategy (str): strategy for payment confirmation. "amount" will
          match receipts on amount only. "notification_order" will match
          receipts in order of payment notifications received by signal.
          "confirmation_order" will match payments in order of confirmation on
          the Mobilecoin blockchain

        Returns:
          bool: boolean representing if expected receipts match actual receipts
        """
        paid = self.payment_receipts
        expected = self.expected_receipts
        if len(paid) != len(expected):
            logging.warning(f"expected {len(expected)} payments, received {len(paid)}")
            return False

        if strategy == "notification_order":
            paid = sorted(paid, key=lambda x: x.signal_timestamp or 0)
        if strategy == "amount":
            paid = sorted(paid, key=lambda x: x.amount or 0)
            expected = sorted(expected, key=lambda x: x[1].amount or 0)

        result = [paid[i] == expected[i][1] for i in range(len(paid))]
        if result:
            return all(result)
        return False


def create_test_definition_file(test: Test) -> JSON:
    """
    Transforms test object to JSON so that it can be stored for re-use.
    This will may local, getpost, and postgres in the future.
    """

    test_json = asdict(test)
    return test_json


def send_n_messages(  # pylint: disable=too-many-arguments
    name: str,
    description: str,
    recipient: str,
    amount: int,
    message: str,
    expected_response: Optional[str] = None,
    delay: float = 1.0,
    order: str = "sequential",
    validate_responses: bool = False,
    timeout: float = 360.0,
) -> Test:
    """
    Auto-definition of test for sending an {amount} of messages to a {receipient}
    This function is a prototype for defining future tests.
    """
    steps = []

    for i in range(amount):
        sender_message = message
        response = None

        if expected_response:
            if message == expected_response:
                response_message = message + " " + str(i + 1)
                sender_message = response_message
                response = TestMessage("tester", response_message, sender=recipient)
            else:
                response = TestMessage("tester", expected_response, sender=recipient)

        steps.append(
            TestStep(
                uid=f"{name}-{i+1}",
                description=f"send message: {sender_message}",
                message=TestMessage(recipient, sender_message),
                expected_response=response,
                delay=delay,
            )
        )
    return Test(name, description, recipient, steps, order, validate_responses, timeout)


def script_test(name: str, recipient: str, script: list[tuple[str, str]]) -> Test:
    """
    Test definition that can be declared using tuples
    """
    return Test(
        name,
        name,
        recipient,
        steps=[
            TestStep(
                uid=f"{name}-{call[:4]}",
                description=f"send message: {call}",
                message=TestMessage(recipient, call),
                expected_response=TestMessage("tester", response, sender=recipient),
                delay=0.2,
            )
            for call, response in script
        ],
        validate_responses=True,
    )


def payments_test(
    name: str,
    recipient: str,
    script: list[
        tuple[
            tuple[str, Optional[int]],
            tuple[Optional[str], Optional[int], Optional[str]],
        ]
    ],
) -> Test:
    steps = []
    for step in script:
        message, send_amount = step[0]
        response, receive_amount, note = step[1]

        receipt = None
        if receive_amount:
            receipt = PaymentReceipt(
                sender=recipient, recipient="tester", amount=receive_amount, note=note
            )

        payment = None
        if send_amount:
            payment = (recipient, send_amount)

        steps.append(
            TestStep(
                uid=f"{name}-{message}",
                description=f"send message: {message}",
                message=TestMessage(recipient, message, payment=payment),
                expected_response=TestMessage("tester", response, sender=recipient),
                expected_receipt=receipt,
                delay=4,
            )
        )

    return Test(name, name, recipient, steps=steps)


imogen = "+12406171474"  # "+12406171657"
echopay = get_secret("ECHOPAY")

ping_test = script_test(
    "ping", imogen, [("/ping", "/pong"), ("/ping 1", "/pong 1"), ("/pong", "OK")]
)

pay_test = payments_test(
    "echopay_test",
    echopay,
    [
        (("/ping", None), ("/pong", None, None)),
        (("/pong", None), ("OK", None, None)),
        (
            ("/pay", None),
            (
                "receipt sent!",
                1000000000,
                "check out this java-free payment notification",
            ),
        ),
    ],
)

redis_test = script_test(
    "redis",
    imogen,
    [
        ("/imagine_nostart foo", "you are #1 in line"),
        ("/list_queue", "foo"),
        ("/dump_queue", "foo"),
        ("/list_queue", "queue empty"),
    ],
)
# todo: /send <number> across two contactbot instances or one with multiple accounts,
# check for reaction

# maybe a signal-cli based test for groups

load_test = send_n_messages(
    name="send_3_messages",
    description="send 20 messages",
    recipient="+12406171615",
    amount=3,
    message="it's okay to be broken",
    delay=3.5,
    timeout=30 * 3.5,
)

acceptance_test = send_n_messages(
    name="test_echobot",
    description="test echobot for correct behavior",
    recipient="+12406171615",
    amount=3,
    message="it's okay to be broken",
    expected_response="it's okay to be broken",
    delay=3.5,
    validate_responses=True,
    timeout=20 * 3.5,
)


class Tiamat(PayBot):
    """
    Bot for running acceptance and load tests of other bots.

    Attributes:
      available_tests (dict[str, Test]): set of available tests to Tiamat
      test (Test): test specification object for current test, should be
      reset to None after each each test
      test_result (TestResult): iestResult object that stores results of
      current test, should be rest to None after each test
      test_running (bool): indicates whether test is running
      test_admin (str): signal number of primary test admin
      secondary_admins (list[str]): list of signal numbers that can also manage
      Tiamat tests
      test_result_log (list[TestResult): list of TestResult objects from past
      tests
      pending_step_results (Queue[StepResult]): FIFO Queue containing
      StepResult objects to be compared against actual messages received by
      bots being tested
      response_queue (Queue[tuple[Message, test, float]): Queue containing
      messages received by bot being tested.
      payment_tasks (list[Task]): List of record_payment tasks
      monitor (Task): response_monitor task that reads incoming messages
      from bot
      test_launcher (Task): test launcher task

    """

    def __init__(
        self,
        admin: str,
        available_tests: list[Test],
        secondary_admins: Optional[list[str]] = None,
    ) -> None:
        """
        Args:
          admin (str): signal number of primary admin
          available_tests (list[Test]): List of test specifications that can be
          run
          secondary_admins: (Optional[list[str]]): List of numbers also
          authorized to run tests with Tiamat
        """
        super().__init__()
        self.available_tests: dict[str, Test] = {
            _test.name: _test for _test in available_tests
        }
        self.test: Optional[Test] = None
        self.test_result: Optional[TestResult] = None
        self.test_running: bool = False
        self.test_admin: str = admin
        self.secondary_admins: Optional[list[str]] = secondary_admins
        self.test_result_log: list[TestResult] = []
        self.pending_step_results: Queue[StepResult] = Queue()
        self.response_queue: Queue[tuple[Message, Test, float]] = Queue()
        self.payment_tasks: list[Task] = []
        self.monitor: Optional[Task] = None
        self.test_launcher: Optional[Task] = None

    @staticmethod
    def is_data_message(response: Message) -> bool:
        if response.blob.get("content", {}).get("source", {}).get("dataMessage"):
            return True
        return False

    async def set_profile(self) -> None:
        profile = {
            "jsonrpc": "2.0",
            "method": "setProfile",
            "id": 666,
            "params": {
                "profile_fields": {
                    "name": {"givenName": "tiamat", "familyName": ""},
                    "mobilecoinAddress": get_secret("MOBADDRESS"),
                    "about": "The many headed dragon helps",
                    "about_emoji": "\N{Rainbow}",
                }
            },
        }
        await self.outbox.put(profile)
        logging.info(profile)

    async def handle_message(self, message: Message) -> Union[Response, None]:
        """
        Handles messages when they arrive. If a test is active and the message
        is from the bot being tested it will be put into a queue to be processed
        by the response_monitor task. If a payment is received by the bot being
        tested, it will launch a record_payment task to verify it.

        It will also listen for messages/payments from test admins and process
        those normally. Messages from any other users are not respondedto.

        Args:
          message (Message): Message received by bot framework

        Returns:
          Union[Response, None]: A Response typed object that is processed
          and sent via the auxin signal client to the sender of the message
        """
        if (
            isinstance(self.test, Test)
            and self.test_running
            and self.test.validate_responses
            and message.source == self.test.recipient
            and self.is_data_message(message)
        ):
            if message.payment:
                logging.info(f"payment message received: {message}")
                payment_task = create_task(
                    self.record_payment(message, self.test.payment_timeout)
                )
                self.payment_tasks.append(payment_task)
            else:
                await self.response_queue.put((message, self.test, time.time()))

        # If you're admin, respond, else, blackhole
        if self.is_admin(message.source):
            logging.info(message)
            if message.payment:
                logging.info(f"payment received - {message}")
                return await super().handle_payment(message)
            return await super().handle_message(message)

        return None

    async def configure_test(self, test: Test) -> None:
        """
        Prepare test configuration by setting new Test definition and TestResult
        objects within the class

        Args:
          test (Test): test definition object
        """

        logging.info(f"attempting to load {test.name}")
        if self.test_running or self.test:
            message = "existing test running, please wait"
            logging.warning(message)

        self.test = test
        self.test_result = TestResult(test=test, test_account=self.bot_number)
        message = f"{test.name} configured, steps: {test.steps} ready to run"
        logging.info(message)

    def is_test_ready(self) -> bool:
        """
        Perform checks prior to launching test to ensure Tiamat is configured
        correctly to launch the test and the test is valid.

        Returns:
          bool: Boolean flag indicating proper test configuration
        """
        if self.test_running:
            logging.warning("Existing test running, aborting run attempt")
            return False
        if not isinstance(self.test, Test):
            logging.warning("No currently loaded test, aborting run attempt")
            return False
        if not isinstance(self.test_result, TestResult):
            logging.warning(
                "Test result object must be configured prior to launching test, aborting"
            )
            return False
        try:
            self.test.validate_self()
        except ValueError:
            logging.warning("Test definition is invalid, please reconfigure")
            return False
        return True

    async def send_sequential_messages(self) -> None:
        """
        Executes sending of messages within the loaded test definition
        """
        assert self.test
        assert self.test_result

        for step in self.test.steps:
            await asyncio.sleep(step.delay)
            logging.debug(f"starting step: {step}")
            step_result = StepResult(
                uid=step.uid,
                message_sent=step.message,
                expected_response=step.expected_response,
            )
            step_result.python_timestamp = time.time()

            if step.message.payment:
                recipient, amount = step.message.payment
                assert amount
                send_receipt = await self.send_payment(
                    recipient, amount, ""
                )  # Type checked when test created
            else:
                rpc_id = await self.send_message(
                    recipient=step.message.recipient,
                    msg=step.message.message,
                    group=step.message.group,
                    endsession=step.message.endsession,
                    attachments=step.message.attachments,  # type: ignore
                    content=step.message.content,
                )
                send_receipt = await self.pending_requests[rpc_id]

            logging.info(f"send receipt is {send_receipt}")
            if isinstance(send_receipt, Message):
                step_result.auxin_timestamp = send_receipt.timestamp / 1000
                step_result.auxin_roundtrip_latency = (
                    step_result.auxin_timestamp - step_result.python_timestamp
                )
            if self.test.validate_responses:
                await self.pending_step_results.put(step_result)
            else:
                self.test_result.step_results.append(step_result)
        logging.info(f"all steps in {self.test.name} executed")

    async def launch_test(self) -> Optional[TestResult]:
        """
        Coroutine that launches a defined test by executing the steps defined
        by that test.

        This will begin sending a sequence of messages (potentially
        including attachments) or payments to the target bot. It also
        initialize a response_monitor task within a class variables which listens
        for replies to those messages and processes them. StepResult objects
        representing expected replies are put into the pending_step_results
        queue for the response_monitor to compare the actual replies wth.

        All tasks should be cancelled and dereferenced when this method completes
        or raises an exception.

        Returns:
            Optional[TestResult]: A TestResult object which stores the
            responses, attachments, and payment receipts received from the
            bot being tested.

        Raises:
            NotImplementedError: Raised if test steps are not specified as
            "sequential" order in the test definition. Future implementations
            may implement other test step orderings.
        """
        assert self.test
        logging.info("Validating test definition & Tiamat pre-test configuration")
        if not self.is_test_ready():
            return None

        logging.info(f"{self.test.name} launching in {self.test.order} order")
        self.test_running = True
        if self.test.validate_responses:
            try:
                self.monitor = create_task(self.response_monitor())
            except Exception:  # pylint: disable=broad-except
                logging.exception("Test monitor task failed, aborting test")
                self.cleanup_test("failed")
        assert self.test_result
        self.test_result.start_time = time.time()
        if self.test.order == "sequential":

            await self.send_sequential_messages()

            if self.test.validate_responses and isinstance(self.monitor, Task):
                logging.info("awaiting remaining test message responses")
                await self.monitor
            if self.test.validate_payments:
                if not self.payment_tasks and self.test.has_payments():
                    wait = 20
                    logging.info(
                        f"waiting for payment receipts to be sent for {wait} seconds"
                    )
                    await asyncio.sleep(wait)
                    if not self.payment_tasks:
                        logging.warning(
                            "No payment notifications received during wait!"
                        )
                await asyncio.gather(*self.payment_tasks)

        else:
            raise NotImplementedError

        return self.cleanup_test()

    async def record_payment(self, message: Message, timeout: float) -> None:
        """
        Logs signal payment message in PaymentReceipt object and attached it to
        test and waits for confirmation of payment on mobilecoin blockchain.
        If the receipt arrives before timeout, confirmation is stored in the
        payment receipt, if not a timeout is logged. Payment receipts are
        stored within the TestResult object for the running test.

        Args:
          message (Message): signal datamessage with payment field
          timeout (float): amount of time to wait for payment receipt confirmation
        """

        if not isinstance(self.test_result, TestResult):
            raise ValueError("test_result must be initialized prior to test")

        logging.info(f"payment message received {message}")
        try:
            receipt = PaymentReceipt(
                sender=message.source,
                recipient=self.bot_number,
                signal_timestamp=time.time(),
                note=message.payment.get("note"),
            )
            logging.info(
                f"receipt is {receipt}, attempting to confirm on the mobilecoin blockchain"
            )
            amount_pmob = await wait_for(
                self.mobster.get_receipt_amount_pmob(message.payment["receipt"]),
                timeout,
            )

            receipt.amount, receipt.confirmation_timestamp = amount_pmob, time.time()
            self.test_result.payment_receipts.append(receipt)
            logging.info(f"payment confirmed on mobilecoin blockchain, {receipt}")
            # create_task(self.write_receipt_to_db(amount_pmob, message))
        except (asyncio.TimeoutError, asyncio.CancelledError):
            receipt.timeout, receipt.timeout_timestamp = (True, time.time())
            self.test_result.payment_receipts.append(receipt)
            logging.warning(f"Payment validation failed before timeout: {receipt}")
            return

    async def write_receipt_to_db(self, amount_pmob: int, message: Message) -> None:
        """
        Log payment receipts for later review.

        Args:
          amount_pmob (int): transaction amount
          message (Message): message payment was sent in
        """
        amount_mob = float(mc_util.pmob2mob(amount_pmob))
        amount_usd_cents = round(amount_mob * await self.mobster.get_rate() * 100)
        await self.mobster.ledger_manager.put_mob_tx(
            message.source,
            amount_usd_cents,
            amount_pmob,
            message.payment.get("note"),
        )

    async def response_monitor(self) -> None:
        """
        Monitors responses sent from bot being tested to messages from Tiamat.
        Specifically reads messages from the response_queue and StepResult
        objects from the pending_step_results queue and compares them to
        determine if expected replies match actual responses.

        """

        logging.info("starting reply monitoring")
        while 1:
            if not isinstance(self.test_result, TestResult):
                raise AttributeError("test result not present, aborting test")
            try:
                response, test, timestamp = await wait_for(
                    self.response_queue.get(), 10
                )
                del test  # shh pylint
                logging.info(f"attempting to validate reply {response}")
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return
            try:
                step_result = await wait_for(self.pending_step_results.get(), 20)
            except asyncio.TimeoutError:
                logging.warning("waiting for corresponding result tracker failed")
                continue
            except asyncio.CancelledError:
                return

            logging.info(f"comparing {step_result} with {response}")
            step_result.response_timestamp = timestamp
            if (
                isinstance(step_result.auxin_timestamp, float)
                and step_result.auxin_timestamp > 0.0
            ):
                step_result.roundtrip_delta = timestamp - step_result.auxin_timestamp

            step_result.actual_response = TestMessage(
                recipient=self.bot_number,
                message=response.full_text,
                group=response.group,
                endsession=False,
                attachments=response.attachments,
                content="",
                sender=response.source,
            )

            if isinstance(step_result.expected_response, TestMessage) and (
                step_result.actual_response.message
                == step_result.expected_response.message
            ):
                step_result.result = "passed"
            else:
                step_result.result = "failed"

            self.test_result.step_results.append(step_result)
            logging.info(f"result: {step_result}")

    @staticmethod
    def validate_test_result(test_result: TestResult) -> str:
        """
        Checks if all payments and messages received match expected output.
        Test is marked as failed unless all outputs match expected outputs.

        Args:
          test_result (TestResult): Test result object to be checked

        Returns:
          str: "passed" if all outputs match expected outputs, else "failed"

        """
        test = test_result.test
        if not isinstance(test_result, TestResult):
            raise TypeError("Cannot validate test result, invalid input passed")
        if not test.validate_responses:
            return "passed"
        for step_result in test_result.step_results:
            if step_result.result != "passed":
                return "failed"
        if not test_result.expected_receipts and test_result.payment_receipts:
            logging.warning(
                "Test didn't expect payments, but received them, test failure"
            )
            return "failed"
        if test_result.expected_receipts:
            if test.validate_payments and not test_result.all_receipts_confirmed():
                return "failed"
            if not test_result.receipts_match(test.payment_validation_strategy):
                return "failed"
        return "passed"

    def cleanup_test(self, pass_or_fail: Optional[str] = None) -> Optional[TestResult]:
        """
        Validate test and cleanup test tasks and data. Cancels any running record_payment
        and response_monitor tasks and dereferences them. Resets all class members
        containing test data to None or falsy state. Validates test and stores
        it in test log.

        Args:
          pass_or_fail Optional[str]: "passed" or "failed" status to assign to
          the task in case of early stopping. Unused if test completes normally.

        Returns:
          Optional[TestResult]: TestResult object.
        """
        logging.info("Calling cleanup routine")
        if isinstance(self.test_launcher, Task) and not self.test_launcher.done():
            self.test_launcher.cancel()
        if isinstance(self.monitor, Task) and not self.monitor.done():
            self.monitor.cancel()
        self.monitor = None
        if self.payment_tasks:
            for task in self.payment_tasks:
                if not task.done():
                    task.cancel()
            self.payment_tasks = []

        result = None
        if isinstance(self.test_result, TestResult):
            result = self.test_result
            if pass_or_fail:
                result.result = pass_or_fail
            else:
                result.result = self.validate_test_result(result)
            result.end_time = time.time()
            if result.start_time != -1:
                result.runtime = result.end_time - result.start_time

            logging.info(result)
            final_result = deepcopy(result)
            self.test_result_log.append(final_result)
            create_task(
                self.send_message(recipient=self.test_admin, msg=repr(final_result))
            )

        self.test_running = False
        self.test = None
        self.test_result = None
        self.test_launcher = None
        self.response_queue = Queue()
        self.pending_step_results = Queue()
        return result

    def is_admin(self, sender: str) -> bool:
        """
        Determine if a message is sent by a test admin

        Args:
          sender (str): signal formatted number message was sent from

        Returns:
          bool: boolean indicating if sender is an admin
        """

        logging.debug(f"sender is {sender} admin is {self.test_admin}")
        if isinstance(self.test_admin, str):
            return sender == self.test_admin
        if isinstance(self.secondary_admins, list):
            return sender in self.secondary_admins
        return True

    async def _launch_test(self, timeout: float) -> None:
        """
        Helper method for launch test to perform test cleanup in case of
        error or timeout.

        Args:
          timeout (float): maximum number of seconds test can run for
        """

        try:
            await wait_for(self.launch_test(), timeout)
        except asyncio.TimeoutError:
            logging.warning("Maximum test runtime reached, test failed")
            self.cleanup_test()
        except asyncio.CancelledError:
            logging.warning("Test being stopped early")
        except Exception:  # pylint: disable=broad-except
            logging.exception("Test execution encountered an error")
            self.cleanup_test()

    async def do_start_test(self, message: Message) -> str:
        """
        Launches specified if available. Shows available tests if test is
        not available.
        """

        test = None
        if self.is_admin(message.source):
            if self.test_running:
                name = self.test.name if self.test else "current test"
                return f"{name} running, please wait until it finishes"
            for name, _test in self.available_tests.items():
                if name in message.text:
                    try:
                        logging.debug("Validating test prior to start")
                        _test.validate_self()
                    except ValueError:
                        return (
                            "Test {_test.name} configured incorrectly, please"
                            "please review test definition and try again"
                        )
                    test = deepcopy(_test)
                    break
            if not test:
                available_tests = await self.do_available_tests(message)
                return f"Specified test not available - {available_tests}"
            try:
                await self.configure_test(test)
                self.test_launcher = create_task(self._launch_test(test.timeout))
            except Exception:  # pylint: disable=broad-except
                logging.exception("Test failed to configure ")
                return "Test failed to configure and launch correctly"
            return f"{test.name} launched"
        return "Not authorized"

    async def run_test_programmatically(self, test: Test) -> TestResult:
        """
        Run test via python invocation
        """

        await self.configure_test(test)
        result = await self.launch_test()
        assert result
        return result

    async def do_stop_test(self, message: Message) -> str:
        """
        Stop current test in progress
        """

        if not self.is_admin(message.source):
            return "Sorry you don't have sufficient privileges to manage tests"
        if self.test:
            name = self.test.name
            self.cleanup_test("failed")
            return f"Test {name} stopped"
        return "No tests to stop!"

    async def do_get_running_tests(self, _: Message) -> str:
        """
        Get test(s) currently running
        """

        if self.test and self.test_running:
            return f"{self.test.name} currently running"
        return "No running tests"

    async def do_available_tests(self, _: Message) -> str:
        """
        Show available tests
        """

        return f"available tests: {', '.join(self.available_tests.keys())}"

    async def do_view_test_results(self, message: Message) -> str:
        """
        View results of past tests
        """

        if not self.test_result_log:
            return "No test records have been logged"
        try:
            selection = int(re.search(r"\d+", message.text).group())  # type: ignore
            test_result = self.test_result_log[selection - 1]
            if "--steps" in message.text:
                return repr(test_result.step_results)
            return repr(test_result)
        except (AttributeError, IndexError, ValueError):
            result_log = self.test_result_log
            results = {i + 1: result_log[i].name for i in range(len(result_log))}
            msg = (
                f"avaliable test logs: {str(results)} {new_line}"
                "type /view_test_results (test number) to access specific test"
                " or /view_test_results (test number) --steps to print that"
                " test's test step results"
            )
            return msg

    async def do_set_profile(self, message: Message) -> None:
        logging.info(f"admin {message.source} setting profile")
        await self.set_profile()


class FakeMessage(Message):
    def __init__(self, **kwargs: Any) -> None:  # pylint: disable=super-init-not-called
        self.__dict__.update(kwargs)


if __name__ == "__main__":
    test_admin = get_secret("TEST_ADMIN") or get_secret("ADMIN")
    logging.info(f"starting tiamat with admin {test_admin}")
    logging.info(f"test_echobot steps are {acceptance_test.steps}")

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = Tiamat(
            admin=test_admin,
            available_tests=[
                load_test,
                ping_test,
                acceptance_test,
                redis_test,
                pay_test,
            ],
        )

    web.run_app(app, port=8080, host="0.0.0.0")
