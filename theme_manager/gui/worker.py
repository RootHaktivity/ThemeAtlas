"""
Background thread pool for non-blocking UI operations.

Callbacks must schedule any Tkinter operations via widget.after() themselves,
because Tkinter is not thread-safe and only the main thread may touch widgets.
"""

import queue
import threading
from typing import Any, Callable, Optional


class BackgroundWorker:
    """Simple thread pool.  Submit tasks; results come back via callbacks."""

    _SENTINEL = object()

    def __init__(self, num_threads: int = 2) -> None:
        self._q: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        for _ in range(num_threads):
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()
            self._threads.append(t)

    # ------------------------------------------------------------------
    def submit(
        self,
        func: Callable[..., Any],
        *args: Any,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        """Queue *func(*args)* to run in a background thread."""
        self._q.put((func, args, on_done, on_error))

    def shutdown(self) -> None:
        """Signal all workers to stop and wait briefly for them to exit."""
        for _ in self._threads:
            self._q.put(self._SENTINEL)
        for t in self._threads:
            t.join(timeout=2)

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is self._SENTINEL:
                break
            func, args, on_done, on_error = item
            try:
                result = func(*args)
                if on_done is not None:
                    on_done(result)
            except Exception as exc:  # noqa: BLE001
                if on_error is not None:
                    on_error(exc)
            finally:
                self._q.task_done()
