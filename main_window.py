from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QPushButton, QFormLayout, QDoubleSpinBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QTextEdit, QGroupBox,
    QLineEdit, QComboBox, QFileDialog, QAbstractItemView
)
from app.editor.intersection_view import IntersectionView
from app.signals.plan import Phase, SignalPlan
from app.simulation.engine import SimulationConfig, run_simulation
from app.integrations.dgis import DGisClient, DGisError
from app.settings import load_settings, save_settings
from app.integrations.road_network import fetch_osm_roads, load_geojson, save_geojson, RoadNetworkError

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TrafficSignalStudio — моделирование светофорных объектов")
        self.resize(1220, 820)
        self.settings = load_settings()
        self.road_network = None
        self.plan = SignalPlan.default()
        tabs = QTabWidget()
        tabs.addTab(self._build_map_tab(), "2ГИС / импорт")
        tabs.addTab(self._build_scheme_tab(), "Схема")
        tabs.addTab(self._build_signals_tab(), "Светофорная программа")
        tabs.addTab(self._build_demand_tab(), "Транспортный поток")
        tabs.addTab(self._build_results_tab(), "Результаты")
        self.tabs = tabs
        self.setCentralWidget(tabs)
        self.statusBar().showMessage("Прототип: проверяйте доступность API и валидируйте расчёты перед инженерным применением.")

    def _build_map_tab(self):
        w = QWidget(); l = QVBoxLayout(w)
        intro = QLabel("2ГИС Places API используется для поиска объектов, но не считается источником дорожной топологии. Для геометрии доступен импорт дорожных линий OpenStreetMap (Overpass) и открытие GeoJSON. Данные проверяйте по лицензии и точности перед инженерным использованием.")
        intro.setWordWrap(True); l.addWidget(intro)
        formbox = QGroupBox("Настройки API 2ГИС"); f = QFormLayout(formbox)
        self.api_key = QLineEdit(self.settings.get("dgis_api_key", ""))
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.query = QLineEdit("перекрёсток")
        self.lat = QDoubleSpinBox(); self.lat.setRange(-90,90); self.lat.setDecimals(6); self.lat.setValue(55.751244)
        self.lon = QDoubleSpinBox(); self.lon.setRange(-180,180); self.lon.setDecimals(6); self.lon.setValue(37.618423)
        self.radius = QSpinBox(); self.radius.setRange(100,50000); self.radius.setValue(1500); self.radius.setSuffix(" м")
        f.addRow("API-ключ", self.api_key); f.addRow("Поисковый запрос", self.query)
        f.addRow("Широта", self.lat); f.addRow("Долгота", self.lon); f.addRow("Радиус", self.radius)
        l.addWidget(formbox)
        buttons = QHBoxLayout()
        save = QPushButton("Сохранить ключ локально"); save.clicked.connect(self._save_key); buttons.addWidget(save)
        search = QPushButton("Поиск объектов в 2ГИС"); search.clicked.connect(self._search_dgis); buttons.addWidget(search)
        l.addLayout(buttons)
        road_box = QGroupBox("Дорожная геометрия (не является импортом из 2ГИС)")
        road_layout = QVBoxLayout(road_box)
        road_note = QLabel("Импортирует линии дорог из OpenStreetMap через публичный Overpass API в пределах небольшого прямоугольника вокруг заданной точки. Это геометрия дорог, не готовая модель полос/фаз.")
        road_note.setWordWrap(True); road_layout.addWidget(road_note)
        road_buttons = QHBoxLayout()
        osm = QPushButton("Импортировать дороги OSM вокруг точки"); osm.clicked.connect(self._import_osm_roads); road_buttons.addWidget(osm)
        geo = QPushButton("Открыть GeoJSON"); geo.clicked.connect(self._open_geojson); road_buttons.addWidget(geo)
        save_geo = QPushButton("Сохранить импорт GeoJSON"); save_geo.clicked.connect(self._save_geojson); road_buttons.addWidget(save_geo)
        road_layout.addLayout(road_buttons)
        l.addWidget(road_box)
        self.dgis_results = QTextEdit(); self.dgis_results.setReadOnly(True)
        self.dgis_results.setPlaceholderText("Здесь появятся результаты поиска 2ГИС или состояние импорта дорожных данных.")
        l.addWidget(self.dgis_results, 1)
        return w

    def _import_osm_roads(self):
        from math import cos, radians
        lat, lon = self.lat.value(), self.lon.value()
        radius_km = min(self.radius.value() / 1000.0, 2.0)
        dlat = radius_km / 111.0
        dlon = radius_km / max(20.0, 111.0 * abs(cos(radians(lat))))
        self.dgis_results.setPlainText("Запрашиваются дорожные линии из OpenStreetMap/Overpass…")
        try:
            self.road_network = fetch_osm_roads(lat-dlat, lon-dlon, lat+dlat, lon+dlon)
            features = self.road_network.get("features", [])
            highways = {}
            for feature in features:
                kind = feature.get("properties", {}).get("highway", "unknown")
                highways[kind] = highways.get(kind, 0) + 1
            summary = "\n".join(f"  {k}: {v}" for k, v in sorted(highways.items()))
            report = (f"Импортировано линейных объектов: {len(features)}\n"
                      f"Источник: OpenStreetMap через Overpass API\n\nТипы highway:\n{summary}\n\n"
                      "Ограничение: это линии дорог, не полосы движения, не направления разрешённых поворотов "
                      "и не контроллер светофора. Для моделирования потребуется очистка/конвертация и проверка геометрии.")
            self.dgis_results.setPlainText(report)
        except RoadNetworkError as exc:
            QMessageBox.warning(self, "Ошибка импорта дорожной сети", str(exc))
            self.dgis_results.setPlainText(str(exc))

    def _open_geojson(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть дорожную сеть GeoJSON", "", "GeoJSON (*.geojson *.json)")
        if not path: return
        try:
            self.road_network = load_geojson(path)
            count = len(self.road_network["features"])
            linear = sum(1 for f in self.road_network["features"] if f.get("geometry", {}).get("type") in ("LineString", "MultiLineString"))
            self.dgis_results.setPlainText(f"Открыт GeoJSON: {path}\nОбъектов: {count}\nЛинейных дорожных объектов: {linear}")
        except RoadNetworkError as exc:
            QMessageBox.warning(self, "Ошибка GeoJSON", str(exc))

    def _save_geojson(self):
        if not self.road_network:
            QMessageBox.information(self, "Нет данных", "Сначала импортируйте дороги или откройте GeoJSON."); return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить дорожную сеть", "road_network.geojson", "GeoJSON (*.geojson)")
        if not path: return
        try:
            if not path.lower().endswith((".geojson", ".json")): path += ".geojson"
            save_geojson(self.road_network, path)
            QMessageBox.information(self, "Экспорт", f"GeoJSON сохранён:\n{path}")
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))

    def _save_key(self):
        self.settings["dgis_api_key"] = self.api_key.text().strip()
        save_settings(self.settings)
        QMessageBox.information(self, "Настройки", "Ключ сохранён в локальном settings.json. Не публикуйте этот файл и не включайте его в общий архив.")

    def _search_dgis(self):
        key = self.api_key.text().strip()
        if not key:
            QMessageBox.warning(self, "Нужен API-ключ", "Получите API-ключ в кабинете разработчика 2ГИС и вставьте его сюда.")
            return
        self.dgis_results.setPlainText("Выполняется запрос к 2ГИС Places API…")
        from PySide6.QtCore import QThread, Signal
        class Worker(QThread):
            done = Signal(object, object)
            def run(inner_self):
                try:
                    client = DGisClient(key)
                    data = client.search(self.query.text().strip(), self.lat.value(), self.lon.value(), self.radius.value())
                    inner_self.done.emit(data, None)
                except Exception as e:
                    inner_self.done.emit(None, str(e))
        self.worker = Worker()
        self.worker.done.connect(self._show_dgis_results)
        self.worker.start()

    def _show_dgis_results(self, data, error):
        if error:
            self.dgis_results.setPlainText(
                "Не удалось получить ответ от 2ГИС.\n"
                f"Причина: {error}\n\n"
                "Проверьте API-ключ, доступ к Places API, сетевое подключение и актуальные условия API."
            )
            return
        items = data.get("result", {}).get("items", [])
        lines = [f"Найдено объектов: {len(items)}", ""]
        for i, item in enumerate(items, 1):
            name = item.get("name", "Без названия")
            address = item.get("address_name", "Адрес не указан")
            point = item.get("point", {})
            coords = ""
            if point:
                coords = f" | координаты: {point.get('lat')}, {point.get('lon')}"
            lines.append(f"{i}. {name}\n   {address}{coords}")
        if not items:
            lines.append("Пустой результат. Попробуйте другой запрос или проверьте параметры поиска.")
        lines += ["", "Примечание: найденные места не являются автоматически импортированной дорожной сетью."]
        self.dgis_results.setPlainText("\n".join(lines))

    def _build_scheme_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        intro = QLabel("Редактор условной схемы перекрёстка. Число полос задаётся отдельно для каждого подхода. Геометрия пока схематическая: импортированные линии OSM/2ГИС автоматически в полосы не преобразуются.")
        intro.setWordWrap(True); layout.addWidget(intro)
        controls = QGroupBox("Полосы по направлениям"); form = QFormLayout(controls)
        self.lane_spins = {}
        for approach in IntersectionView.APPROACHES:
            spin = QSpinBox(); spin.setRange(1, 8); spin.setValue(2); spin.setSuffix(" полос(ы)")
            spin.valueChanged.connect(lambda value, name=approach: self.scheme.set_lane_count(name, value))
            self.lane_spins[approach] = spin
            form.addRow(approach, spin)
        layout.addWidget(controls)
        self.scheme = IntersectionView(); layout.addWidget(self.scheme, 1)
        buttons = QHBoxLayout()
        save = QPushButton("Сохранить схему…"); save.clicked.connect(self._save_intersection); buttons.addWidget(save)
        load = QPushButton("Загрузить схему…"); load.clicked.connect(self._load_intersection); buttons.addWidget(load)
        layout.addLayout(buttons)
        return w

    def _build_signals_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        note = QLabel("Редактируйте любое количество фаз. Время красного и зелёного мигающего включено в расчётную длину цикла. Это редактор плана, а не проверка конфликтов сигналов по ГОСТ.")
        note.setWordWrap(True); layout.addWidget(note)
        self.phase_table = QTableWidget(0, 6)
        self.phase_table.setHorizontalHeaderLabels(["Название фазы", "Зелёный, с", "Зелёный мигающий, с", "Жёлтый, с", "Красный, с", "Межфазный, с"])
        self.phase_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.phase_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.phase_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.phase_table, 1)
        buttons = QHBoxLayout()
        add = QPushButton("Добавить фазу"); add.clicked.connect(self._add_phase); buttons.addWidget(add)
        remove = QPushButton("Удалить выбранную фазу"); remove.clicked.connect(self._remove_phase); buttons.addWidget(remove)
        apply = QPushButton("Проверить и применить план"); apply.clicked.connect(self._apply_phases); buttons.addWidget(apply)
        save = QPushButton("Сохранить план…"); save.clicked.connect(self._save_plan); buttons.addWidget(save)
        load = QPushButton("Загрузить план…"); load.clicked.connect(self._load_plan); buttons.addWidget(load)
        layout.addLayout(buttons)
        self.phase_summary = QLabel(); layout.addWidget(self.phase_summary)
        self._populate_phase_table(self.plan.phases)
        self._apply_phases(show_error=False)
        return w

    def _populate_phase_table(self, phases):
        self.phase_table.setRowCount(len(phases))
        for r, phase in enumerate(phases):
            values = [phase.name, phase.green_seconds, phase.flashing_green_seconds,
                      phase.yellow_seconds, phase.red_seconds, phase.intergreen_seconds]
            for c, value in enumerate(values):
                self.phase_table.setItem(r, c, QTableWidgetItem(str(value)))

    def _add_phase(self):
        row = self.phase_table.rowCount()
        if row >= 32:
            QMessageBox.information(self, "Лимит фаз", "Можно добавить не более 32 фаз."); return
        self.phase_table.insertRow(row)
        for col, value in enumerate([f"Фаза {row + 1}", 20, 3, 3, 0, 2]):
            self.phase_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.phase_table.selectRow(row)

    def _remove_phase(self):
        row = self.phase_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Выберите фазу", "Сначала выберите строку фазы."); return
        if self.phase_table.rowCount() <= 1:
            QMessageBox.warning(self, "Нельзя удалить", "В плане должна остаться хотя бы одна фаза."); return
        self.phase_table.removeRow(row)
        self._renumber_empty_phase_names()

    def _renumber_empty_phase_names(self):
        for r in range(self.phase_table.rowCount()):
            item = self.phase_table.item(r, 0)
            if item is None or not item.text().strip():
                self.phase_table.setItem(r, 0, QTableWidgetItem(f"Фаза {r + 1}"))

    def _read_phase_table(self):
        phases = []
        for r in range(self.phase_table.rowCount()):
            cells = [self.phase_table.item(r, c) for c in range(6)]
            if any(cell is None for cell in cells):
                raise ValueError(f"Фаза {r + 1}: заполните все поля.")
            try:
                phases.append(Phase(cells[0].text().strip(), int(cells[1].text()), int(cells[3].text()),
                                    int(cells[5].text()), int(cells[2].text()), int(cells[4].text())))
            except ValueError as exc:
                raise ValueError(f"Фаза {r + 1}: времена должны быть целыми числами.") from exc
        return phases

    def _apply_phases(self, show_error=True):
        try:
            self.plan = SignalPlan(self._read_phase_table())
            self.phase_summary.setText(f"Фаз: {len(self.plan.phases)} · Расчётная длина цикла: {self.plan.cycle_seconds} с")
            if hasattr(self, "scheme"):
                self.scheme.update()
            return True
        except Exception as e:
            self.phase_summary.setText("План не применён: исправьте значения в таблице.")
            if show_error:
                QMessageBox.warning(self, "Некорректный план", str(e))
            return False

    def _save_plan(self):
        if not self._apply_phases(): return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить светофорный план", "signal_plan.json", "План JSON (*.json)")
        if path:
            try:
                self.plan.save(path)
                QMessageBox.information(self, "Сохранение", "Светофорный план сохранён.")
            except OSError as exc:
                QMessageBox.critical(self, "Ошибка сохранения", str(exc))

    def _load_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить светофорный план", "", "План JSON (*.json)")
        if not path: return
        try:
            loaded = SignalPlan.load(path)
            self._populate_phase_table(loaded.phases)
            self._apply_phases()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Ошибка загрузки плана", str(exc))

    def _save_intersection(self):
        import json
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить схему перекрёстка", "intersection.json", "Схема JSON (*.json)")
        if not path: return
        data = {"schema_version": 1, "lane_counts": {k: v.value() for k, v in self.lane_spins.items()}}
        try:
            with open(path, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Сохранение", "Схема перекрёстка сохранена.")
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))

    def _load_intersection(self):
        import json
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить схему перекрёстка", "", "Схема JSON (*.json)")
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            counts = data["lane_counts"]
            for approach, spin in self.lane_spins.items():
                spin.setValue(int(counts.get(approach, 2)))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Ошибка загрузки схемы", str(exc))

    def _build_demand_tab(self):
        w = QWidget(); outer = QVBoxLayout(w); box = QGroupBox("Параметры сценария"); f = QFormLayout(box)
        self.ns = QSpinBox(); self.ns.setRange(0,5000); self.ns.setValue(900); self.ns.setSuffix(" авт./ч")
        self.ew = QSpinBox(); self.ew.setRange(0,5000); self.ew.setValue(700); self.ew.setSuffix(" авт./ч")
        self.heavy = QDoubleSpinBox(); self.heavy.setRange(0,100); self.heavy.setValue(10); self.heavy.setSuffix(" %")
        self.duration = QSpinBox(); self.duration.setRange(60,7200); self.duration.setValue(1800); self.duration.setSuffix(" с")
        self.saturation = QSpinBox(); self.saturation.setRange(900,2400); self.saturation.setValue(1800); self.saturation.setSuffix(" авт./ч")
        self.seed = QSpinBox(); self.seed.setRange(1,999999); self.seed.setValue(42)
        for label, widget in [("Поток Север—Юг",self.ns),("Поток Восток—Запад",self.ew),("Доля тяжёлых ТС",self.heavy),("Длительность",self.duration),("Насыщенная интенсивность",self.saturation),("Seed сценария",self.seed)]: f.addRow(label,widget)
        outer.addWidget(box)
        note = QLabel("Агрегированная демонстрационная модель: потоки по двум осям. Повороты, пешеходы, погода и влияние тяжёлых ТС на динамику не моделируются.")
        note.setWordWrap(True); outer.addWidget(note)
        run = QPushButton("Запустить моделирование"); run.clicked.connect(self._run); outer.addWidget(run); outer.addStretch()
        return w

    def _build_results_tab(self):
        w = QWidget(); l = QVBoxLayout(w); self.results_text = QTextEdit(); self.results_text.setReadOnly(True)
        self.results_text.setPlaceholderText("Запустите моделирование на вкладке «Транспортный поток».")
        l.addWidget(self.results_text); return w

    def _apply_phases(self):
        try:
            phases = []
            for r, name in enumerate(["Север—Юг", "Восток—Запад"]):
                green = int(self.phase_table.item(r,1).text())
                yellow = int(self.phase_table.item(r,2).text())
                intergreen = int(self.phase_table.item(r,3).text())
                if green < 5 or yellow < 1 or intergreen < 0:
                    raise ValueError("Зелёный ≥ 5 с, жёлтый ≥ 1 с, межфазный интервал ≥ 0.")
                phases.append(Phase(name,green,yellow,intergreen))
            self.plan = SignalPlan(phases)
            self.phase_summary.setText(f"Расчётная длина цикла: {self.plan.cycle_seconds} с")
            self.scheme.update()
            return True
        except Exception as e:
            QMessageBox.warning(self, "Некорректный план", str(e)); return False

    def _run(self):
        if not self._apply_phases(): return
        cfg = SimulationConfig(self.ns.value(), self.ew.value(), self.heavy.value(),
                               self.duration.value(), self.saturation.value(), self.seed.value())
        result = run_simulation(cfg, self.plan)
        self.results_text.setPlainText(result.to_report())
        self.tabs.setCurrentIndex(4)
