# Modified from https://stackoverflow.com/a/79059059
#
# See the LICENSE file for more information.
"""
Thread pool implementation using a queue for task management.

This module provides the QueueThreadPool class for managing a pool of worker
threads that execute tasks submitted to a queue.
"""

from __future__ import annotations

import logging
from queue import Queue
from threading import Thread
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class QueueThreadPool:
    """
    A thread pool implementation using a queue for task management.

    Attributes
    ----------
    _pool_size : int
        The number of worker threads in the pool.
    _task_queue : Queue
        The queue holding tasks to be executed by the worker threads.
    _shutting_down : bool
        Indicates whether the thread pool is shutting down.

    Methods
    -------
    submit(fn, *args)
        Submit a task to the thread pool.
    shutdown(wait=True)
        Shut down the thread pool, optionally waiting for tasks to complete.

    """

    def __init__(self, pool_size: int) -> None:
        """
        Initialize the thread pool with a given pool size.

        Args:
            pool_size (int): The number of worker threads in the pool.

        """
        self._pool_size = pool_size
        self._task_queue: Queue[
            tuple[Callable[..., object], tuple[object]] | None
        ] = Queue()
        self._shutting_down = False
        for _ in range(self._pool_size):
            Thread(target=self._executor, daemon=True).start()

    def __enter__(self) -> Self:
        """
        Enter the runtime context related to this object.

        Returns:
            Self: The DictSpider instance itself.

        """
        return self

    def __exit__(
        self,
        typ: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """
        Exit the runtime context and clean up resources.

        Args:
            typ (type[BaseException] | None): Exception type, if any.
            exc (BaseException | None): Exception instance, if any.
            tb (TracebackType | None): Traceback, if any.

        """
        self.shutdown()

    def _terminate_threads(self) -> None:
        """Tell threads to terminate."""
        # No new tasks in case this is an immediate shutdown:
        self._shutting_down = True

        for _ in range(self._pool_size):
            self._task_queue.put(None)
        self._task_queue.join()  # Wait for all threads to terminate

    def shutdown(self, wait: bool = True) -> None:  # noqa: FBT001, FBT002
        """
        Shut down the thread pool, optionally waiting for tasks to complete.

        Args:
            wait (bool): If True, wait for the task queue to become empty
            before shutting down. Defaults to True.

        """
        if wait:
            # Wait until the task queue quiesces (becomes empty).
            # Running tasks may be continuing to submit tasks to the queue but
            # the expectation is that at some point no more tasks will be added
            # and we wait for the queue to become empty:
            self._task_queue.join()
        self._terminate_threads()

    def submit(self, fn: Callable[..., object], *args: object) -> None:
        """
        Submit a task to the thread pool.

        Args:
            fn (Callable[..., object]): The function to execute.
            *args (object): Arguments to pass to the function.

        """
        if self._shutting_down:
            return
        self._task_queue.put((fn, args))

    def _executor(self) -> None:
        while True:
            task = self._task_queue.get()
            if task is None:  # sentinel
                self._task_queue.task_done()
                return
            fn, args = task
            try:
                fn(*args)
            except Exception:
                logger.exception("Exception in thread pool task:")
            # Show this work has been completed:
            self._task_queue.task_done()
