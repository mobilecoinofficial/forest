import logging
from statistics import mean
import time
import asyncio
from pprint import pformat
from typing import Any, Optional
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
class FullServiceTester:
    """
    Async test fixture class. Methods with 'test_' are isolated tests
    meant to run until event loop completion so that they can be run
    in CI/CD. A new instance should be made for each test.

    Attributes:
      results (dict): Container of individual test results to be evaluated by a
      test framework
      stats (dict): Pretty formatted summary of test statistics
    """

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
            "runtime": end - start,
            "confirmation_time": end - confirmation_start,
            "data": result.get("result"),
            "pass": status == "tx_status_succeeded",
        }
        self.results[tx_log_id] = test_result

    async def send_n_txs(
        self, outputs: list[list[tuple[str, int]]], wait: float = 0.02
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
            tasks += [create_task(self.send_and_verify_tx(output))]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def send_and_verify_tx(self, outputs: list[tuple[str, int]]) -> None:
        """
        Send a transaction and store its success/failure
        """

        def log_result(_id: str, result: Optional[dict]) -> None:
            if isinstance(result, dict) and "error" in result:
                result = result.get("error")
            self.results[_id] = {
                "pass": False,
                "data": result,
                "runtime": time.time() - start,
            }

        start = time.time()
        prop = await m.build_transaction(
            account_id=sender_acct,
            addresses_and_values=outputs,
            comment="tx_stress_test",
        )
        if not isinstance(prop, dict) or "error" in prop:
            _id = "build_transaction_failure" + str(time.time() * 1000)
            log_result(_id, prop)
            return

        tx_proposal = prop.get("result", {}).get("tx_proposal")
        tx_log_id = prop.get("result", {}).get("transaction_log_id")
        result = await m.submit_transaction(
            tx_proposal, account_id=sender_acct, comment="tx_stress_test"
        )
        if not isinstance(result, dict) or "error" in result:
            log_result(tx_log_id, result)
            return

        await self.confirm_tx_success(tx_log_id, start)

    def summarize(self, name: str, start: float) -> None:
        tests = self.results.values()
        num_pass = len([True for test in tests if test["pass"]])
        self.stats = {
            "name": name,
            "test_runtime": time.time() - start,
            "pass": num_pass,
            "fail": len(tests) - num_pass,
            "step_runtime_avg": mean([test["runtime"] for test in tests]),
        }
        logging.info("test summary:\n %s", pformat(self.stats))

    ### Individual tests to run in a testing framework (pytest,etc..)
    async def test_concurrent_tx_send(
        self, num: int, amt: float, wait: float = 0.1
    ) -> dict[str, Any]:
        start = time.time()
        await self.send_n_txs([[(receiver_address, pmob(amt))]] * num, wait)
        self.summarize("test_concurrent_tx_send", start)
        return self.results


if __name__ == "__main__":
    fst = FullServiceTester()
    run(fst.test_concurrent_tx_send(20, 0.001))
