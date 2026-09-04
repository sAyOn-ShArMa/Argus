"""Bounded visible-process monitor for proactive notifications."""

from __future__ import annotations

from threading import Event, Lock, Thread

from argus.proactive.engine import ProactiveEngine


class ProactiveMonitor:
    def __init__(self, engine: ProactiveEngine) -> None:
        self._engine = engine
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self.last_error: str | None = None

    def check_now(self):
        with self._lock:
            return self._engine.run_once()

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self.check_now()
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
        self._thread = Thread(
            target=self._run,
            name="argus-proactive-monitor",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        interval = self._engine.config.poll_interval_seconds
        while not self._stop.wait(interval):
            try:
                self.check_now()
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
