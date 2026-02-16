# Modified from https://stackoverflow.com/a/79059059
#
# See the LICENSE file for more information.
"""thread pool executor implementation using a queue for task management."""

from __future__ import annotations

from concurrent.futures import Executor, Future
from queue import Empty, Queue
from threading import Thread
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    P = ParamSpec("P")
    T = TypeVar("T")


class QueueThreadPoolExecutor(Executor):
    """A thread pool executor using a queue for task management."""

    def __init__(self, max_workers: int) -> None:
        """
        Initialize the thread pool executor.

        Args:
            max_workers (int): The number of worker threads in the pool.

        """
        self._max_workers = max_workers
        self._task_queue: Queue[
            tuple[
                Callable[..., Any],
                tuple[Any, ...],
                dict[str, object],
                Future[Any],
            ]
            | None
        ] = Queue()
        self._shutting_down = False
        for _ in range(self._max_workers):
            Thread(target=self._executor, daemon=True).start()

    def _terminate_threads(self) -> None:
        self._shutting_down = True

        for _ in range(self._max_workers):
            self._task_queue.put(None)
        self._task_queue.join()

    def shutdown(
        self,
        wait: bool = True,  # noqa: FBT001, FBT002
        *,
        cancel_futures: bool = False,
    ) -> None:
        """
        Shut down the thread pool executor.

        Args:
            wait (bool): If True, wait for the task queue to become empty
            before shutting down. Defaults to True.
            cancel_futures (bool): If True, cancel all pending futures that
            have not yet started executing. Defaults to False.

        """
        if cancel_futures:
            try:
                while True:
                    task = self._task_queue.get_nowait()
                    if task is None:
                        self._task_queue.put(None)
                        self._task_queue.task_done()
                        break
                    _, _, _, future = task
                    try:
                        future.cancel()
                    finally:
                        self._task_queue.task_done()
            except Empty:
                pass

        if wait:
            self._task_queue.join()

        self._terminate_threads()

    def submit(
        self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs
    ) -> Future[T]:
        """
        Submit a task to the thread pool executor.

        Args:
            fn (Callable[P, T]): The function to execute.
            *args (P.args): Arguments to pass to the function.
            **kwargs (P.kwargs): Keyword arguments to pass to the function.

        Returns:
            Future[T]: A Future representing the execution of the task.

        Raises:
            RuntimeError: If the executor is shut down. Note that this
            executor allows submissions after ``shutdown()`` has been
            called; a ``RuntimeError`` is raised only once the executor
            begins terminating threads and no longer accepts new tasks.

        """
        if self._shutting_down:
            msg = "cannot schedule new futures after shutdown"
            raise RuntimeError(msg)

        future: Future[T] = Future()
        self._task_queue.put((fn, args, kwargs, future))
        return future

    def _executor(self) -> None:
        while True:
            task = self._task_queue.get()
            if task is None:
                self._task_queue.task_done()
                return
            fn, args, kwargs, future = task
            try:
                if not future.set_running_or_notify_cancel():
                    continue
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                future.set_exception(exc)
            else:
                future.set_result(result)
            finally:
                self._task_queue.task_done()
