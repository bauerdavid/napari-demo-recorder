"""The napari dock widget: record actions to YAML, play and render demos.

Play / Render run in a separate process (a fresh viewer), so the demo never changes the
session you are working in.
"""

from __future__ import annotations

import sys
from pathlib import Path

from qtpy.QtCore import QProcess, Qt
from qtpy.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.format import DemoError, Step, load_demo
from ..core.recorder import Recorder, steps_to_demo
from ..core.selectors import INTERNAL_PREFIX
from .adapter import NapariAdapter


def _summary(step: Step) -> str:
    p = step.params
    for key in ("button", "widget", "text", "keys", "layer", "path", "code", "seconds"):
        if key in p:
            val = p[key]
            if isinstance(val, list):
                val = " > ".join(map(str, val))
            txt = f"{step.name}: {val}"
            if "value" in p:
                txt += f" = {p['value']}"
            if "parent" in p:
                txt += f"   [{p['parent']}]"
            return txt
    if "canvas" in p:
        n = len(p["canvas"]) if p["canvas"] and isinstance(p["canvas"][0], list) else 1
        return f"{step.name}: canvas ({n} point{'s' if n > 1 else ''})"
    return f"{step.name}: {', '.join(f'{k}={v}' for k, v in p.items())}"


class DemoRecorderWidget(QWidget):
    def __init__(self, napari_viewer):
        super().__init__()
        self.viewer = napari_viewer
        self.setObjectName(INTERNAL_PREFIX + "widget")
        self.recorder: Recorder | None = None
        self.process: QProcess | None = None

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("demo file (*.demo.yaml)")
        browse = QPushButton("Open…")
        browse.clicked.connect(self._open)
        row.addWidget(self.path_edit)
        row.addWidget(browse)
        lay.addLayout(row)

        btns = QHBoxLayout()
        self.record_btn = QPushButton("● Record")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._toggle_record)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(lambda: self._run("play"))
        self.render_btn = QPushButton("Render GIF")
        self.render_btn.clicked.connect(lambda: self._run("render"))
        for b in (self.record_btn, self.play_btn, self.render_btn):
            btns.addWidget(b)
        lay.addLayout(btns)

        self.status = QLabel("Open a demo, or press Record and use napari.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.steps_list = QListWidget()
        lay.addWidget(self.steps_list, 3)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.log, 2)
        self.path_edit.editingFinished.connect(self._load)

    # ------------------------------------------------------------------ files
    @property
    def path(self) -> Path | None:
        text = self.path_edit.text().strip()
        return Path(text) if text else None

    def _open(self) -> None:
        fname, _ = QFileDialog.getOpenFileName(self, "Open demo", "", "Demo files (*.yaml *.yml)")
        if fname:
            self.path_edit.setText(fname)
            self._load()

    def _load(self) -> None:
        self.steps_list.clear()
        if self.path is None or not self.path.exists():
            return
        try:
            spec = load_demo(self.path)
        except DemoError as err:
            self.status.setText(f"<b>Invalid demo:</b> {err}")
            return
        for section, steps in (("setup", spec.setup), ("", spec.steps)):
            for s in steps:
                item = QListWidgetItem(("setup · " if section else "") + _summary(s))
                if section:
                    item.setForeground(Qt.GlobalColor.gray)
                self.steps_list.addItem(item)
        self.status.setText(f"{len(spec.steps)} steps · output: {spec.output_path()}")

    # ------------------------------------------------------------------ recording
    def _toggle_record(self, on: bool) -> None:
        if on:
            window = self.window()
            adapter = NapariAdapter()
            adapter.attach(window, self.viewer)
            self.recorder = Recorder(window, adapter, ignore=[self])
            self.recorder.on_step = lambda s: self.steps_list.addItem("● " + _summary(s))
            self.recorder.start()
            self.record_btn.setText("■ Stop")
            self.status.setText("Recording… use napari as you want it shown, then press Stop.")
            return
        if self.recorder is None:
            return
        steps = self.recorder.stop()
        self.recorder = None
        self.record_btn.setText("● Record")
        self._save_recording(steps)

    def _save_recording(self, steps: list[Step]) -> None:
        path = self.path
        if path is None:
            fname, _ = QFileDialog.getSaveFileName(self, "Save demo", "demo.demo.yaml", "Demo files (*.yaml)")
            if not fname:
                self.status.setText("Recording discarded.")
                return
            path = Path(fname)
            self.path_edit.setText(str(path))
        setup: list[Step] = []
        if path.exists():
            answer = QMessageBox.question(
                self,
                "Demo exists",
                f"Append the {len(steps)} recorded steps to {path.name}?\n(No = replace its steps)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            try:
                spec = load_demo(path)
                setup = spec.setup
                if answer == QMessageBox.StandardButton.Yes:
                    steps = spec.steps + steps
            except DemoError:
                pass
        text = steps_to_demo(steps, app="napari", window=self.window(), setup=setup)
        path.write_text(text, encoding="utf-8")
        self._load()
        self.status.setText(f"Saved {len(steps)} steps to {path}. Add a setup (data, plugins) and render.")

    # ------------------------------------------------------------------ play / render
    def _run(self, command: str) -> None:
        if self.path is None or not self.path.exists():
            self.status.setText("Open or record a demo first.")
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.log.clear()
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self.log.appendPlainText(bytes(proc.readAllStandardOutput()).decode(errors="replace").rstrip())
        )
        proc.finished.connect(lambda code, _status: self.status.setText(f"{command} finished (exit {code})"))
        proc.start(sys.executable, ["-m", "napari_demo_recorder", command, str(self.path)])
        self.process = proc
        self.status.setText(f"{command} running…")
