import asyncio
import functools
import logging
from typing import Any, Awaitable, Optional, TypeVar, Tuple, Callable

T = TypeVar("T")


def create_handled_task(
    coroutine: Awaitable[T],
    *,
    message: str,
    message_args: Tuple[Any, ...] = (),
    error_handler: Callable = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> asyncio.Task[T]:
    """
    Wrap a normal asyncio task with facilities that logs error on failure, and
    optionally restart the task

    args:
      message (str): log error message
      message_args (Tuple[Any, ...]): log message args
      error_handler (Callable): function or coroutine to call upon task failure
      loop (asyncio.AbstractEventLoop): event loop to use if outside main loop

    Returns:
      asyncio.Task: Task with error handler attached
    """
    if loop:
        task = loop.create_task(coroutine)
    else:
        logging.info("creating handled task")
        task = asyncio.create_task(coroutine)

    if error_handler and not asyncio.iscoroutinefunction(error_handler):
        logging.warning(
            "Warning: Error handler passed was not a coroutine, it will not be invoked"
        )

    task.add_done_callback(
        functools.partial(
            _handle_task_result,
            error_handler=error_handler,
            message=message,
            message_args=message_args,
        )
    )
    return task


def _handle_task_result(
    task: asyncio.Task,
    *,
    message: str,
    message_args: Tuple[Any, ...] = (),
    error_handler: Optional[Callable] = None,
) -> None:
    """
    Done callback which logs the error and handles/restarts the task if an
    error handler is specified
    """
    name = task.get_name()
    logging.info("Task %s has called its done callback", name)
    try:
        result = task.result()
        logging.info("result of task %s, was %s", name, result)
    except asyncio.CancelledError:
        logging.info("task %s was cancelled", name)
    except Exception:  # pylint: disable=broad-except
        logging.exception(message, *message_args)
        if callable(error_handler):
            logging.info("error handler invoking for task %s", name)
            if asyncio.iscoroutinefunction(error_handler):
                asyncio.create_task(error_handler())
            else:
                error_handler()
