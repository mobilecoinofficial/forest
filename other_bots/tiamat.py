#!/usr/bin/python3.9
import time
import re
import logging
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from asyncio import Queue, wait_for, create_task
from typing import Optional, Union
from aiohttp import web
from forest.utils import get_secret
from forest.core import Bot, Message, Response, app


@dataclass
class TestMessage:
    """Represents a message object sent to Auxin"""

    recipient: Optional[str] = None
    message: Optional[str] = None
    group: Optional[str] = None
    endsession: bool = False
    attachments: Optional[list[str]] = None
    content: Optional[str] = None
    sender: Optional[str] = None


@dataclass
class TestStep:
    """Configuration for an individual test message"""

    uid: str
    description: str
    message: TestMessage
    expected_response: Optional[TestMessage] = None
    delay: int = 3


@dataclass
class Test:
    """Configuration for a multi-message test"""

    name: str
    description: str
    steps: list[TestStep]
    order: str = "sequential"
    validate_responses: bool = False
    timeout: int = 3600
    recipient: Optional[str] = None

    def __post_init__(self) -> None:
        if self.order not in ("sequential", "paralllel"):
            raise ValueError("Order must be either sequential or parallel")


@dataclass
class StepResult:
    """Result of individual test step"""

    uid: Optional[Union[None, str]] = None
    message_sent: Optional[TestMessage] = None
    expected_response: Optional[TestMessage] = None
    actual_response: Optional[TestMessage] = None
    result: Optional[str] = None
    python_timestamp: float = None
    auxin_timestamp: float = None
    auxin_roundtrip_latency: float = None
    send_delay: int = None
    response_timestamp: int = None
    roundtrip_delta: int = None


@dataclass
class TestResult:
    """Result of a multi-step test"""

    test: Test = field(repr=False)
    name: str = None
    test_account: str = "tester"
    step_results: list[StepResult] = field(default_factory=list, repr=False)
    result: str = "pre_initialization"
    start_time: int = -1
    end_time: int = -1
    runtime: int = -1

    def __post_init__(self):
        self.name = self.test.name


def create_test_definition_file(test: Test) -> None:
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
    expected_response: Union[str, None],
    delay: Union[int, float] = 1,
    order: str = "sequential",
    validate_responses: bool = False,
    timeout: Union[int, float] = 360,
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
    return Test(name, description, steps, order, validate_responses, timeout, recipient)


load_test = send_n_messages(
    name="send_3_messages",
    description="send 20 messages",
    recipient="+12406171615",
    amount=3,
    message="it's okay to be broken",
    expected_response=None,
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


class Tiamat(Bot):
    def __init__(
        self,
        *args,
        test: Optional[Test] = None,
        admin: Optional[Union[str, list[str]]] = None,
    ) -> None:
        super().__init__(*args)
        self.test: Test = test
        self.test_result: TestResult = None
        self.test_running: bool = False
        self.test_admin: str = admin
        self.test_result_log: list[TestResult] = []
        self.pending_step_results: Queue[StepResult] = Queue()
        self.response_queue: Queue[(Message, Test, float)] = Queue()

    @staticmethod
    def is_data_message(response: Message):
        if response.blob.get("content", {}).get("source", {}).get("dataMessage"):
            return True
        return False

    async def handle_message(self, message: Message) -> Response:
        """
        If message is response to text, hanlde it according to test definition.
        If it is another type of message, deal with it.
        """
        # If incoming message is test response from target bot and a test
        # that's configured to validate responses is running, put message
        # in queue, otherwise ignore
        if (
            isinstance(self.test, Test)
            and self.test_running
            and self.test.validate_responses
            and message.source == self.test.recipient
            and self.is_data_message(message)
        ):
            await self.response_queue.put((message, self.test, time.time()))
            return None

        # If you're admin, respond, else, blackhole
        if self.is_admin(message.source):
            return await super().handle_message(message)

    async def configure_test(self, test: Test = None) -> None:
        """Prepare test configuration within bot"""

        logging.info(f"attempting to load {test.name}")
        if self.test_running or self.test:
            message = "existing test running, please wait"
            logging.warning(message)
            return message

        self.test = test
        self.test_result = TestResult(test=test, test_account=self.bot_number)
        message = f"{test.name} configured, {len(test.steps)} steps ready to run"
        logging.info(message)

    async def launch_test(self) -> None:
        if self.test_running:
            logging.warning("Existing test running, aborting run attempt")
            return

        test_monitor = None
        logging.info(f"{self.test.name} launching in {self.test.order} order, oh myyyy")
        self.test_running = True
        if self.test.validate_responses:
            try:
                test_monitor = create_task(
                    wait_for(self.response_monitor(), self.test.timeout)
                )
            except asyncio.TimeoutError:
                await self.cleanup_test(test_monitor, "failed")
            except Exception as e:
                logging.exception("Test monitor task failed, aborting test: %s", e)
                await self.cleanup_test(test_monitor, "failed")

        self.test_result.start_time = time.time()
        if self.test.order == "sequential":

            for step in self.test.steps:
                logging.debug(f"starting step: {step}")
                step_future = self.send_message(
                    recipient=step.message.recipient,
                    msg=step.message.message,
                    group=step.message.group,
                    endsession=step.message.endsession,
                    attachments=step.message.attachments,
                    content=step.message.content,
                )
                await asyncio.sleep(step.delay)
                step_result = StepResult(
                    uid=step.uid,
                    message_sent=step.message,
                    expected_response=step.expected_response,
                )
                step_result.python_timestamp = time.time()
                rpc_id = await step_future
                send_receipt = await self.pending_requests[rpc_id]
                logging.info(f"send receipt is {send_receipt}")
                step_result.auxin_timestamp = send_receipt.timestamp / 1000
                step_result.auxin_roundtrip_latency = (
                    step_result.auxin_timestamp - step_result.python_timestamp
                )
                if self.test.validate_responses:
                    await self.pending_step_results.put(step_result)
                else:
                    self.test_result.step_results.append(step_result)
                logging.info(f"all steps in {self.test.name} executed")

            if self.test.validate_responses:
                logging.info("awaiting remaining test message responses")
                await test_monitor
        else:
            raise NotImplementedError

        await self.cleanup_test(test_monitor)

    async def response_monitor(self, test: Test = None) -> None:
        """
        Response monitor for test, stops after test timeout
        """
        logging.info("starting reply monitoring")
        while 1:

            try:
                response, test, timestamp = await wait_for(
                    self.response_queue.get(), 10
                )
                logging.info(f"attempting to validate reply {response}")
            except asyncio.TimeoutError:
                return
            try:
                step_result = await wait_for(self.pending_step_results.get(), 20)
            except asyncio.TimeoutError:
                logging.warning("waiting for corresponding result tracker failed")
                continue
            logging.info(f"comparing {step_result} with {response}")
            step_result.response_timestamp = timestamp
            if step_result.auxin_timestamp > 0:
                step_result.roundtrip_delta = timestamp - step_result.auxin_timestamp

            step_result.actual_response = TestMessage(
                recipient=self.bot_number,
                message=response.text,
                group=response.group,
                endsession=False,
                attachments=response.attachments,
                content=None,
                sender=response.source,
            )
            if (
                step_result.actual_response.message
                == step_result.expected_response.message
            ):
                step_result.result = "passed"

            self.test_result.step_results.append(step_result)
            logging.info(f"result: {step_result}")

    @staticmethod
    def validate_test_result(test_result: TestResult):
        if not isinstance(test_result, TestResult):
            raise TypeError("Cannot validate test result, invalid input passed")
        if not test_result.test.validate_responses:
            return "passed"
        for step_result in test_result.step_results:
            if step_result.result != "passed":
                return "failed"
        return "passed"

    async def cleanup_test(
        self, test_monitor: asyncio.Task = None, pass_or_fail: str = None
    ):
        if test_monitor and not test_monitor.done():
            if test_monitor.exception():
                test_monitor.cancel()
            if pass_or_fail == "failed":
                test_monitor.cancel()

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
            await self.send_message(recipient=self.test_admin, msg=repr(final_result))

        self.test_running = False
        self.test = None
        self.test_result = None

    def is_admin(self, sender):
        if isinstance(self.test_admin, str):
            return sender == self.test_admin
        if isinstance(self.test_admin, list):
            return sender in self.test_admin
        return True

    async def do_start_test(self, message: Message):
        if self.is_admin(message.source):
            if self.test_running:
                if self.test:
                    return f"{self.test.name} running, please wait until it finishes"
                return "current test running, please wait until it finishes"
            if ("load_test" in message.text) and ("acceptance_test" in message.text):
                return "cannot specify more than one test"
            if not message.text:
                return "must specify at least one test"
            if "load_test" in message.text:
                test = deepcopy(load_test)
            if "acceptance_test" in message.text:
                test = deepcopy(acceptance_test)
            try:
                await self.configure_test(test)
            except Exception as e:
                logging.warning("Test failed to configure with error %s", e)

            asyncio.create_task(self.launch_test())
            return f"{test.name} launched"

    async def do_stop_test(self, message: Message) -> str:
        if not self.is_admin(message.source):
            return "sorry you don't have sufficient privileges to manage tests"
        if self.test:
            return "this will stop ongoing tests"
        return "no tests to stop!"

    async def do_get_running_tests(self, message: Message):
        if self.test is None or not self.test_running:
            return "No running tests"
        if self.test and self.test_running:
            return f"{self.test.name} currently running"

    async def do_available_tests(self, message: Message) -> str:
        return "load_test and acceptance_test are available"

    async def do_view_test_results(self, message: Message) -> str:
        if not self.test_result_log:
            return "No test records have been logged"
        try:
            selection = int(re.search(r"\d+", message.text).group())
            test_result = self.test_result_log[selection - 1]
            if "--steps" in message.text:
                return repr(test_result.step_results)
            return repr(test_result)
        except (AttributeError, IndexError, ValueError):
            result_log = self.test_result_log
            results = {i + 1: result_log[i].name for i in range(len(result_log))}
            msg = (
                f"avaliable test logs: {str(results)}\n"
                "type /view_test_results (test number) to access specific test or"
                "/view_test_results (test number) --steps to print that test's test step results"
            )
            return msg


if __name__ == "__main__":
    test_admin = get_secret("TEST_ADMIN")
    logging.info(f"starting tiamat with admin {test_admin}")

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = Tiamat(admin=test_admin)

    web.run_app(app, port=8080, host="0.0.0.0")
