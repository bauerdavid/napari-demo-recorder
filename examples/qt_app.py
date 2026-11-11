"""A tiny plain-Qt app ("Sketchpad") used to show (and test) that the core is not napari-only.

Demo: ``examples/qt_app.demo.yaml`` with ``app: {factory: "qt_app:make_window"}``.
"""

from __future__ import annotations

from qtpy.QtCore import QPoint, Qt
from qtpy.QtGui import QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSlider,
    QWidget,
)

COLORS = {"Black": "#202020", "Orange": "#ff8c1a", "Teal": "#1aa39a", "Purple": "#7a4ad8"}


class Sketch(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("sketch")
        self.setMinimumSize(300, 300)
        self.strokes: list[tuple[str, int, list[QPoint]]] = []
        self.color = "Black"
        self.brush_width = 6
        self.mirror = False

    def mousePressEvent(self, e):  # noqa: N802
        self.strokes.append((self.color, self.brush_width, [e.position().toPoint() if hasattr(e, "position") else e.pos()]))
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self.strokes and e.buttons():
            self.strokes[-1][2].append(e.position().toPoint() if hasattr(e, "position") else e.pos())
            self.update()

    def clear(self):
        self.strokes.clear()
        self.update()

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#fbfaf6"))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for color, width, pts in self.strokes:
            pen = QPen(QColor(COLORS[color]), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            for a, b in zip(pts, pts[1:]):
                p.drawLine(a, b)
                if self.mirror:
                    w = self.width()
                    p.drawLine(QPoint(w - a.x(), a.y()), QPoint(w - b.x(), b.y()))
        p.end()


class SketchWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sketchpad")
        self.sketch = Sketch()
        self.setCentralWidget(self.sketch)

        panel = QWidget()
        form = QFormLayout(panel)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 30)
        self.size_slider.setValue(6)
        self.size_slider.valueChanged.connect(lambda v: setattr(self.sketch, "brush_width", v))
        self.color_box = QComboBox()
        self.color_box.addItems(list(COLORS))
        self.color_box.currentTextChanged.connect(lambda c: setattr(self.sketch, "color", c))
        self.title_edit = QLineEdit("Untitled")
        self.title_edit.textChanged.connect(self.setWindowTitle)
        self.mirror_box = QCheckBox("Mirror strokes")
        self.mirror_box.toggled.connect(self._mirror)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.sketch.clear)
        form.addRow("Brush size:", self.size_slider)
        form.addRow("Color:", self.color_box)
        form.addRow("Title:", self.title_edit)
        form.addRow(self.mirror_box)
        form.addRow(self.clear_button)
        dock = QDockWidget("Tools")
        dock.setObjectName("tools")
        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&New", self.sketch.clear)
        export = file_menu.addMenu("Export")
        self.exported: list[str] = []
        export.addAction("As PNG", lambda: self.exported.append("png"))
        export.addAction("As SVG", lambda: self.exported.append("svg"))
        view_menu = self.menuBar().addMenu("&View")
        act = view_menu.addAction("Mirror mode")
        act.setCheckable(True)
        act.toggled.connect(self.mirror_box.setChecked)

    def _mirror(self, on: bool):
        self.sketch.mirror = on
        self.sketch.update()


def make_window() -> SketchWindow:
    return SketchWindow()


if __name__ == "__main__":
    app = QApplication([])
    w = make_window()
    w.show()
    app.exec()
