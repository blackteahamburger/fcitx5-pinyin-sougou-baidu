from queue import Queue
from threading import Thread


class QueueThreadPool:
	def __init__(self, pool_size):
		self._pool_size = pool_size
		self._task_queue = Queue()
		self._shutting_down = False
		for _ in range(self._pool_size):
			Thread(target=self._executor, daemon=True).start()

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		self.shutdown()

	def _terminate_threads(self):
		"""Tell threads to terminate."""
		# No new tasks in case this is an immediate shutdown:
		self._shutting_down = True

		for _ in range(self._pool_size):
			self._task_queue.put(None)
		self._task_queue.join()  # Wait for all threads to terminate

	def shutdown(self, wait=True):
		if wait:
			# Wait until the task queue quiesces (becomes empty).
			# Running tasks may be continuing to submit tasks to the queue but
			# the expectation is that at some point no more tasks will be added
			# and we wait for the queue to become empty:
			self._task_queue.join()
		self._terminate_threads()

	def submit(self, fn, *args):
		if self._shutting_down:
			return
		self._task_queue.put((fn, args))

	def _executor(self):
		while True:
			task = self._task_queue.get()
			if task is None:  # sentinel
				self._task_queue.task_done()
				return
			fn, args = task
			try:
				fn(*args)
			except Exception as e:
				print(e)
			# Show this work has been completed:
			self._task_queue.task_done()
