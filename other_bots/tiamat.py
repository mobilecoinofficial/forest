#!/usr/bin/python3.9
import time
import re
import logging
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from asyncio import Queue, Task, wait_for, create_task
from typing import Any, Optional, Union
from aiohttp import web
from forest.utils import get_secret
from forest.core import Bot, Message, Response, JSON, app

new_line: str = "\n"


@dataclass
class TestMessage:
    """Represents a message object sent to Auxin"""

    recipient: str
    message: Optional[str] = None
    group: Optional[str] = None
    endsession: bool = False
    attachments: Optional[list[dict[str, str]]] = None
    content: str = ""
    sender: Optional[str] = None


@dataclass
class TestStep:
    """Configuration for an individual test message"""

    uid: str
    description: str
    message: TestMessage
    expected_response: Optional[TestMessage] = None
    delay: float = 3.0


@dataclass
class Test:
    """Configuration for a multi-message test"""

    name: str
    description: str
    recipient: str
    steps: list[TestStep]
    order: str = "sequential"
    validate_responses: bool = False
    timeout: float = 3600.0

    def __post_init__(self) -> None:
        if self.order not in ("sequential", "paralllel"):
            raise ValueError("Order must be either sequential or parallel")


@dataclass
class StepResult:
    """Result of individual test step"""

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
    """Result of a multi-step test"""

    test: Test = field(repr=False)
    name: Optional[str] = None
    test_account: str = "tester"
    step_results: list[StepResult] = field(default_factory=list, repr=False)
    result: str = "pre_initialization"
    start_time: float = -1.0
    end_time: float = -1.0
    runtime: float = -1.0

    def __post_init__(self) -> None:
        self.name = self.test.name


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


imogen = "+12406171474"  # "+12406171657"

ping_test = script_test(
    "ping", imogen, [("/ping", "/pong"), ("/ping 1", "/pong 1"), ("/pong", "OK")]
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


class Tiamat(Bot):
    def __init__(
        self,
        admin: str,
        available_tests: list[Test],
        test: Optional[Test] = None,
        secondary_admins: Optional[list[str]] = None,
    ) -> None:
        super().__init__()
        self.available_tests = {_test.name: _test for _test in available_tests}
        self.test: Optional[Test] = test
        self.test_result: Optional[TestResult] = None
        self.test_running: bool = False
        self.test_admin: str = admin
        self.secondary_admins: Optional[list[str]] = secondary_admins
        self.test_result_log: list[TestResult] = []
        self.pending_step_results: Queue[StepResult] = Queue()
        self.response_queue: Queue[tuple[Message, Test, float]] = Queue()

    @staticmethod
    def is_data_message(response: Message) -> bool:
        if response.blob.get("content", {}).get("source", {}).get("dataMessage"):
            return True
        return False

    async def handle_message(self, message: Message) -> Union[Response, None]:
        """
        If message is response to text, hanlde it according to test definition.
        If it is another type of message, deal with it.
        """
        if (
            isinstance(self.test, Test)
            and self.test_running
            and self.test.validate_responses
            and message.source == self.test.recipient
            and self.is_data_message(message)
        ):
            await self.response_queue.put((message, self.test, time.time()))

        # If you're admin, respond, else, blackhole
        if self.is_admin(message.source):
            return await super().handle_message(message)

        return None

    async def configure_test(self, test: Test) -> None:
        """Prepare test configuration within bot"""

        logging.info(f"attempting to load {test.name}")
        if self.test_running or self.test:
            message = "existing test running, please wait"
            logging.warning(message)

        self.test = test
        self.test_result = TestResult(test=test, test_account=self.bot_number)
        message = f"{test.name} configured, {len(test.steps)} steps ready to run"
        logging.info(message)

    async def launch_test(self) -> Optional[TestResult]:
        # maybe merge into configure_test
        if self.test_running:
            logging.warning("Existing test running, aborting run attempt")
            return None

        if not self.test:
            logging.warning("No currently loaded test, aborting run attempt")
            return None

        if not self.test_result:
            logging.warning(
                "Test result object must be configured prior to launching test, aborting"
            )
            return None

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
            except Exception:  # pylint: disable=broad-except
                logging.exception("Test monitor task failed, aborting test")
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
                    attachments=[
                        attachment["id"] for attachment in step.message.attachments
                    ]
                    if step.message.attachments
                    else None,
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

            if self.test.validate_responses and isinstance(test_monitor, Task):
                logging.info("awaiting remaining test message responses")
                await test_monitor
        else:
            raise NotImplementedError

        return await self.cleanup_test(test_monitor)

    async def response_monitor(self) -> None:
        """
        Response monitor for test, stops after test timeout
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

            except asyncio.TimeoutError:
                return
            try:
                step_result = await wait_for(self.pending_step_results.get(), 20)
            except asyncio.TimeoutError:
                logging.warning("waiting for corresponding result tracker failed")
                continue
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
    ) -> Optional[TestResult]:
        if test_monitor and not test_monitor.done():
            if test_monitor.exception():
                test_monitor.cancel()
            if pass_or_fail == "failed":
                test_monitor.cancel()
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
            await self.send_message(recipient=self.test_admin, msg=repr(final_result))

        self.test_running = False
        self.test = None
        self.test_result = None
        return result

    def is_admin(self, sender: str) -> bool:
        if isinstance(self.test_admin, str):
            return sender == self.test_admin
        if isinstance(self.secondary_admins, list):
            return sender in self.secondary_admins
        return True

    async def do_start_test(self, message: Message) -> str:
        if self.is_admin(message.source):
            if self.test_running:
                name = self.test.name if self.test else "current test"
                return f"{name} running, please wait until it finishes"
            if ("load_test" in message.text) and ("acceptance_test" in message.text):
                return "cannot specify more than one test"
            for name, _test in self.available_tests.items():
                if name in message.text:
                    test = deepcopy(_test)
                    break
            else:
                return "must specify at least one test"
            try:
                await self.configure_test(test)
            except Exception:  # pylint: disable=broad-except
                logging.exception("Test failed to configure")
                return "Test failed to configure correctly"

            asyncio.create_task(self.launch_test())
            return f"{test.name} launched"
        return "Not authorized"

    async def run_test_programmatically(self, test: Test) -> TestResult:
        await self.configure_test(test)
        result = await self.launch_test()
        assert result
        return result

    async def do_stop_test(self, message: Message) -> str:
        if not self.is_admin(message.source):
            return "sorry you don't have sufficient privileges to manage tests"
        if self.test:
            return "this will stop ongoing tests"
        return "no tests to stop!"

    async def do_get_running_tests(self, _: Message) -> str:
        if self.test and self.test_running:
            return f"{self.test.name} currently running"
        return "No running tests"

    async def do_available_tests(self, _: Message) -> str:
        return f"available tests: {', '.join(self.available_tests.keys())}"

    async def do_view_test_results(self, message: Message) -> str:
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


class FakeMessage(Message):
    def __init__(self, **kwargs: Any) -> None:  # pylint: disable=super-init-not-called
        self.__dict__.update(kwargs)


if __name__ == "__main__":
    test_admin = get_secret("TEST_ADMIN") or get_secret("ADMIN")
    logging.info(f"starting tiamat with admin {test_admin}")

    @app.on_startup.append
    async def start_wrapper(our_app: web.Application) -> None:
        our_app["bot"] = bot = Tiamat(
            admin=test_admin,
            available_tests=[load_test, ping_test, acceptance_test, redis_test],
        )
        await bot.run_test_programmatically(redis_test)

    web.run_app(app, port=9090, host="0.0.0.0")
