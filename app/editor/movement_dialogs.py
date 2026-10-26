"""Dialogs for configuring lane arrows and assigning movements to signal phases."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QComboBox, QCheckBox, QScrollArea, QWidget, QGridLayout, QDialogButtonBox,
    QHeaderView
)

TURN_OPTIONS = (
    "Прямо",
    "Налево",
    "Направо",
    "Прямо, налево",
    "Прямо, направо",
    "Налево, направо",
    "Прямо, налево, направо",
)
TURN_GROUPS = {
    "Прямо": ("Прямо",),
    "Налево": ("Налево",),
    "Направо": ("Направо",),
    "Прямо, налево": ("Прямо", "Налево"),
    "Прямо, направо": ("Прямо", "Направо"),
    "Налево, направо": ("Налево", "Направо"),
    "Прямо, налево, направо": ("Прямо", "Налево", "Направо"),
}


class LaneArrowDialog(QDialog):
    """Set permitted movements for each individual lane."""
    def __init__(self, lane_counts, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Разрешённые направления по полосам")
        self.resize(620, 560)
        self.lane_counts = dict(lane_counts)
        self.rows = []
        layout = QVBoxLayout(self)
        note = QLabel(
            "Для каждой полосы укажите стрелки на дорожной разметке. "
            "Интенсивность и фазовые назначения будут доступны только для выбранных манёвров."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Подход", "Полоса", "Разрешённые манёвры"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(sum(self.lane_counts.values()))
        row = 0
        for approach, count in self.lane_counts.items():
            for lane in range(1, count + 1):
                self.table.setItem(row, 0, QTableWidgetItem(approach))
                self.table.setItem(row, 1, QTableWidgetItem(str(lane)))
                combo = QComboBox()
                combo.addItems(TURN_OPTIONS)
                combo.setCurrentText(current.get((approach, lane), "Прямо, налево, направо"))
                self.table.setCellWidget(row, 2, combo)
                self.rows.append((approach, lane, combo))
                row += 1
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {(approach, lane): TURN_GROUPS[combo.currentText()]
                for approach, lane, combo in self.rows}


class PhaseMovementDialog(QDialog):
    """Select exact lane-turn and pedestrian-crossing IDs for one phase."""
    def __init__(self, movements, pedestrians, selected_ids, phase_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Назначения фазы: {phase_name}")
        self.resize(700, 640)
        self.checks = {}
        layout = QVBoxLayout(self)
        note = QLabel(
            "Отметьте движения, которым эта фаза разрешает движение. "
            "Каждая строка содержит подход, номер полосы и манёвр. "
            "Назначения сохраняются в плане и используются при визуализации и расчёте."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        grid = QGridLayout(body)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        grid.addWidget(QLabel("Транспортные движения"), 0, 0, 1, 2)
        row = 1
        for item in movements:
            key = item.movement_id
            label = f"{item.approach}, полоса {item.lane} — {item.turn}"
            check = QCheckBox(label)
            check.setChecked(key in selected_ids)
            grid.addWidget(check, row, 0, 1, 2)
            self.checks[key] = check
            row += 1
        grid.addWidget(QLabel("Пешеходные переходы"), row, 0, 1, 2)
        row += 1
        for item in pedestrians:
            key = item.movement_id
            check = QCheckBox(f"Переход {item.crossing}")
            check.setChecked(key in selected_ids)
            grid.addWidget(check, row, 0, 1, 2)
            self.checks[key] = check
            row += 1
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_ids(self):
        return [key for key, check in self.checks.items() if check.isChecked()]
