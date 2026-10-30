"""Background work for the GUI: long operations run as cancellable ``QgsTask`` objects so QGIS stays
responsive; results and errors are delivered back on the main thread."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

LOG_TAG = "AGSGeoModel"


class Cancelled(Exception):
    pass


class FunctionTask(QgsTask):
    """Run ``fn(progress)`` in a worker thread. ``progress(fraction, message)`` updates the task
    manager and raises :class:`Cancelled` if the user cancelled the task."""

    message = pyqtSignal(str)

    def __init__(
        self,
        description: str,
        fn: Callable[[Callable[[float, str], None]], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException | None, str], None] | None = None,
        cancellable: bool = True,
    ):
        flags = QgsTask.Flag.CanCancel if cancellable else QgsTask.Flag(0)
        super().__init__(description, flags)
        self.fn = fn
        self.on_done = on_done
        self.on_error = on_error
        self.value: Any = None
        self.error: BaseException | None = None
        self.trace = ""

    def _progress(self, fraction: float, msg: str = "") -> None:
        if self.isCanceled():
            raise Cancelled()
        self.setProgress(max(0.0, min(100.0, 100.0 * float(fraction))))
        if msg:
            self.message.emit(msg)

    def run(self) -> bool:  # worker thread: no GUI access here
        try:
            self.value = self.fn(self._progress)
            return True
        except Cancelled:
            return False
        except Exception as exc:  # reported on the main thread in finished()
            self.error = exc
            self.trace = traceback.format_exc()
            return False

    def finished(self, ok: bool) -> None:  # main thread
        if ok:
            if self.on_done:
                self.on_done(self.value)
            return
        if self.error is not None:
            QgsMessageLog.logMessage(f"{self.description()} failed:\n{self.trace}", LOG_TAG, Qgis.MessageLevel.Critical)
        if self.on_error:
            self.on_error(self.error, self.trace)


class TaskRunner:
    """Keeps references to running tasks (a task object that is garbage collected crashes QGIS)."""

    def __init__(self):
        self.tasks: list[FunctionTask] = []

    def start(self, task: FunctionTask) -> FunctionTask:
        self.tasks = [
            t for t in self.tasks if t.status() not in (QgsTask.TaskStatus.Complete, QgsTask.TaskStatus.Terminated)
        ]
        self.tasks.append(task)
        QgsApplication.taskManager().addTask(task)
        return task

    def cancel_all(self) -> None:
        for t in self.tasks:
            if t.status() not in (QgsTask.TaskStatus.Complete, QgsTask.TaskStatus.Terminated):
                t.cancel()
        self.tasks = []

    def busy(self) -> bool:
        return any(
            t.status() in (QgsTask.TaskStatus.Queued, QgsTask.TaskStatus.OnHold, QgsTask.TaskStatus.Running)
            for t in self.tasks
        )


def log(msg: str, level=Qgis.MessageLevel.Info) -> None:
    QgsMessageLog.logMessage(msg, LOG_TAG, level)
