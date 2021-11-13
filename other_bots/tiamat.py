#!/usr/bin/python3.9
import time
import logging
from dataclasses import dataclass
import asyncio
from asyncio import Queue, Future
from typing import Optional
from forest.core import Bot, Message, AuxinMessage


@dataclass
class StepResult:
    """Result of individual test step"""

    uid: str
    expected_response: AuxinMessage = None
    actual_response: AuxinMessage = None
    result: str = None
    python_timestamp: int = None
    auxin_timestamp: int = None


@dataclass
class TestResult:
    """Result of a multi-step test"""

    step_results: list[StepResult]
    result: str


@dataclass
class TestMessage:
    """Represents a message object sent to Auxin"""

    recipient: Optional[str] = None
    message: Optional[str] = None
    group: Optional[str] = None
    endsession: bool = False
    attachments: Optional[list[str]] = None
    content: Optional[str] = None


@dataclass
class TestStep:
    """Configuration for an individual test message"""

    uid: str
    description: str
    message: Message
    expected_response: Optional[AuxinMessage] = None
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

    def __post_init__(self):
        if self.order not in ("sequential", "paralllel"):
            raise ValueError("Order must be either sequential or parallel")


def create_test_definition_file(test: Test) -> None:
    """
    Transforms test object to JSON so that it can be stored for re-use.
    This will may local, getpost, and postgres in the future.
    """
    test_json = test.asdict()
    return test_json


def send_n_messages(
    name: str,
    description: str,
    recipient: str,
    amount: int,
    message: str,
    expected_response: str,
    delay: int = 1,
    order="sequential",
    validate_responses=False,
    timeout=3600
) -> Test:
    """
    Auto-definition of test for sending an {amount} of messages to a {receipient}
    This function is a prototype for defining future tests.
    """
    steps = []

    for i in range(amount):
        if message == expected_response:
            expected_response = message + " " + {i + 1}
        message = message + " " + {i + 1}

        expected_response = AuxinMessage(
            {"content": {"source": {"dataMessage": {"body": expected_response}}}}
        )
        steps += [
            TestStep(
                uid=f"{name}-{i+1}",
                description=f"send message {message}",
                message=TestMessage(recipient, message),
                expected_response=expected_response,
                delay=delay,
            )
        ]
    return Test(name, description, steps, order, validate_responses, timeout)


load_test = send_n_messages(
    name="send_20_messages",
    description="send 20 messages",
    recipient="+12406171615",
    amount=20,
    message="it's okay to be broken",
    expected_response=None,
    delay=3.5,
)

acceptance_test = send_n_messages(
    name="test_echobot",
    description="test echobot for correct behavior",
    recipient="+12406171615",
    amount=10,
    message="it's okay to be broken",
    expected_response="it's okay to be broken",
    delay=3.5,
)


class Tiamat(Bot):
    def __init__(self, *args, test: Test, admin: "str" = None) -> None:
        super().__init__(*args)
        self.test: Test = test
        self.actions: list[tuple(Future, TestStep)] = []
        self.responses: dict[str, list[Message]] = {}  # Responses for given test
        self.response_queue: Queue(Message) = Queue()
        self.pending_step_results: Queue(TestResult) = Queue()
        self.step_results: dict = {}
        self.test_running = False
        self.test_admin = admin

    @staticmethod
    def isDataMessage(response: Message):
        if response.blob.get("content", {}).get("source", {}).get("dataMessage"):
            return True
        return False

    async def handle_messages(self) -> None:
        """
        Reads responses from auxin and takes sends to response
        handler
        """
        async for response in self.auxincli_output_iter():
            self.pending_response_tasks = [
                task for task in self.pending_response_tasks if not task.done()
            ] + [asyncio.create_task(self.handle_response(response))]

    async def handle_response(self, response: Message) -> None:
        """
        If message is response to text, hanlde it according to test definition.
        If it is another type of message, deal with it.
        """

        if response.id and response.id in self.pending_requests:
            self.pending_requests[response.id].set_result(response)
            return None

        try:
            # If incoming message is test response from target bot
            if response.source == self.test.recipient and self.isDataMessage(response):
                await self.response_queue.put((response, self.test, time.time()))
                return None
        except:
            pass  # Temporary

        try:
            # If you're anyone else than the service being tested, respond
            # normally
            self.handle_message(self, Message)
        except:
            pass  # Temporary

    async def prepare_test(self, test: Test = None) -> None:
        """Prepare a series of test coroutines for execution"""

        logging.info("loading {test.name}")

        if test is None:
            test = self.test
        else:
            self.test = test
        self.responses[test.name] = []

        for step in test.steps:
            self.actions.append(
                (
                    self.send_message(
                        step.message.recipient,
                        step.message.message,
                        step.message.group,
                        step.message.endsession,
                        step.message.attachments,
                        step.message.content,
                    ),
                    TestStep,
                )
            )

        logging.info("Test loaded, {len(test.steps)} steps ready to run")

    async def launch_test(self) -> None:
        logging.info("{test} launching in {test.order} order, oh myyyy")

        asyncio.wait_for(self.response_monitor(self.test), self.test.timeout)
        if self.test.order == "sequential":
            for action in self.actions:
                step_future, step = action
                logging.debug(f"starting step: {step}")
                step_result = StepResult(step.uid, step.expected_result)
                asyncio.sleep(step.delay)
                step_result.python_timestamp = time.time()
                rpc_id = await step_future
                send_receipt = await self.pending_requests[rpc_id]
                step_result.auxin_timestamp = send_receipt.timestamp
                await self.pending_step_results.put(step_result)

    async def response_monitor(self, test: Test) -> None:
        """
        Response monitor for test, stops after test timeout
        """
        while 1:
            response, test, timestamp = await self.response_queue.get()
            self.responses[test.name].append(response)
            if test.validate_responses is True:
                step_result = await self.pending_step_results.get()
                step_result.actual_response = response
                if (
                    step_result.actual_response.message
                    == step_result.expected_response.message
                ):
                    step_result.result = "pass"
                logging.info(f"result: {step_result}")

    async def do_stop_test(self, message: Message) -> str:
        if not self.test_admin:
            return "This will stop test in progress"
        if self.test_admin == message.source:
            return "This will stop test in progress if you're admin"
        return "sorry you don't have sufficient privileges to stop this test"

    async def do_get_running_status(self, message: Message) -> str:
        # Get test status
        return "This will list running tests"

    async def do_get_tests(self, message: Message) -> str:
        # Find available test
        return "This will find available tests"

    async def do_schedule_test(self, message: Message) -> str:
        # Enqueue a test
        return "This will schedule tests in the future"


# if __name__ == "__main__":

#    @app.on_startup.append
#    async def start_wrapper(out_app: web.Application) -> None:
#        out_app["bot"] = Forest()
#
#    group_routing_manager = GroupRoutingManager()
#    web.run_app(app, port=8080, host="0.0.0.0")
