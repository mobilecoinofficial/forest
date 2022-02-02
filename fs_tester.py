import logging
import sys
from statistics import mean
import time
import asyncio
from pprint import pformat
from typing import Any, Optional, Callable
from dataclasses import dataclass, field
from asyncio import create_task
from asyncio import run
from forest.utils import get_secret
from forest.payments_monitor import Mobster
import mc_util

m = Mobster()
sender_acct = get_secret("SENDER_ACCOUNT")
receiver_address = get_secret("RECEIVER_ADDRESS")
pmob = mc_util.mob2pmob


@dataclass
class FullServiceHelper:
    """
    Async test fixture class. Methods with 'test_' are isolated tests
    meant to run until event loop completion so that they can be run
    in CI/CD. A new instance should be made for each test.

    Attributes:
      results (dict): Container of individual test results to be evaluated by a
      test framework
      stats (dict): Pretty formatted summary of test statistics
    """

    test_name: str = ""
    start_time: float = 0.0
    results: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    async def confirm_tx_success(self, tx_log_id: str, start: float) -> None:
        """
        Confirm transaction success
        """
        confirmation_start = time.time()
        for i in range(30):
            result = await m.get_transaction_log(tx_log_id)
            status = result.get("result", {}).get("transaction_log", {}).get("status")
            if status == "tx_status_succeeded":
                break
            await asyncio.sleep(0.5)
        end = time.time()
        test_result = {
            "test": self.test_name,
            "runtime": end - start,
            "confirmation_time": end - confirmation_start,
            "data": result.get("result"),
            "pass": status == "tx_status_succeeded",
        }
        logging.info("tx_result %s", status)
        self.results[tx_log_id] = test_result

    async def send_n_txs(
        self, outputs: list[list[tuple[str, int]]], wait: float = 0.02, **params: Any
    ) -> None:
        """
        Send N transactions asynchronously

        args:
          output_list: list of address_and_value pairs to sent in a tx
        """
        logging.info("sending %s txs in %s secs", len(outputs), len(outputs) * wait)
        tasks: list[asyncio.Task] = []
        for output in outputs:
            await asyncio.sleep(wait)
            tasks += [create_task(self.send_and_verify_tx(output, **params))]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def is_error(self, _id: "str", result: Optional[dict], start: float) -> bool:
        if isinstance(result, dict):
            if "result" in result:
                return False
            if "error" in result:
                result = result.get("error")
        self.results[_id] = {
            "test": self.test_name,
            "pass": False,
            "data": result,
            "runtime": time.time() - start,
        }
        return True

    async def send_and_verify_tx(
        self, outputs: list[tuple[str, int]], **params: Any
    ) -> None:
        """
        Send a transaction and store its success/failure
        """
        start = time.time()
        if "submit" in params and params["submit"] == True:
            prop = await m.build_transaction(
                account_id=sender_acct,
                addresses_and_values=outputs,
                comment="tx_stress_test_build_and_submit",
                submit=True,
            )
            log_id = (
                prop.get("result", {})
                .get("transaction_log", {})
                .get("transaction_log_id")
            )
            if not log_id:
                log_id = "build_transaction_failure" + str(time.time() * 1000)
            if not await self.is_error(log_id, prop, start):
                await self.confirm_tx_success(log_id, start)
            return

        prop = await m.build_transaction(
            account_id=sender_acct,
            addresses_and_values=outputs,
            comment="tx_stress_test_build_then_submit",
            log=True,
            **params
        )
        if await self.is_error("build_tx_failure" + str(time.time()), prop, start):
            return

        tx_proposal = prop.get("result", {}).get("tx_proposal")
        tx_log_id = prop.get("result", {}).get("transaction_log_id")
        result = await m.submit_transaction(tx_proposal, comment="tx_stress_test")
        if await self.is_error(tx_log_id, result, start):
            return

        await self.confirm_tx_success(tx_log_id, start)

    def summarize(self) -> None:
        tests = self.results.values()
        num_pass = len([True for test in tests if test["pass"]])
        self.stats = {
            "name": self.test_name,
            "test_runtime": time.time() - self.start_time,
            "pass": num_pass,
            "fail": len(tests) - num_pass,
            "step_runtime_avg": mean([test["runtime"] for test in tests]),
        }
        logging.info("test summary:\n %s", pformat(self.stats))


@dataclass
class FullServiceTester(FullServiceHelper):

    ### Individual tests to run in a testing framework (pytest,etc..)
    async def test_concurrent_build_then_submit(
        self, num: int, amt: float, wait: float = 0.1
    ) -> dict[str, Any]:
        self.test_name = sys._getframe(0).f_code.co_name
        self.start_time = time.time()
        await self.send_n_txs([[(receiver_address, pmob(amt))]] * num, wait)
        self.summarize()
        return self.results

    async def test_concurrent_build_and_submit(
        self, num: int, amt: float, wait: float = 0.1, submit: bool = True
    ) -> dict[str, Any]:
        self.start_time = time.time()
        self.test_name = sys._getframe(0).f_code.co_name
        await self.send_n_txs(
            [[(receiver_address, pmob(amt))]] * num, wait, submit=True
        )
        self.summarize()
        return self.results


fst = FullServiceTester()

if __name__ == "__main__":
    run(fst.test_concurrent_build_and_submit(25, 0.001, 0.1))
