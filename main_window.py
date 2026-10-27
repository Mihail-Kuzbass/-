from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QPushButton, QFormLayout, QDoubleSpinBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QTextEdit, QGroupBox,
    QLineEdit, QComboBox, QFileDialog, QAbstractItemView
)
from app.editor.intersection_view import IntersectionView
from app.signals.plan import Phase, SignalPlan
from app.simulation.engine import SimulationConfig, run_simulation
from app.simulation.detailed import MovementDemand, PedestrianDemand, simulate_detailed
from app.editor.traffic_scene import TrafficScene
from app.signs import SIGN_CATALOG
from app.editor.osm_road_preview import OSMRoadPreview
from app.editor.movement_dialogs import LaneArrowDialog, PhaseMovementDialog
from app.integrations.dgis import DGisClient, DGisError
from app.settings import load_settings, save_settings
from app.integrations.road_network import fetch_osm_roads, infer_intersection_approaches, load_geojson, save_geojson, RoadNetworkError

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TrafficSignalStudio — моделирование светофорных объектов")
        self.resize(1420, 900)
        self.setMinimumSize(1120, 740)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f3f6fa; color: #202b3c; font-family: "Segoe UI"; font-size: 10pt; }
            QTabWidget::pane { border: 1px solid #cbd5e1; background: #ffffff; top: -1px; }
            QTabBar::tab { background: #e2e8f0; color: #334155; padding: 10px 16px; margin-right: 3px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #ffffff; color: #075985; font-weight: 600; border-bottom: 3px solid #0284c7; }
            QGroupBox { background: #ffffff; border: 1px solid #d5deea; border-radius: 8px; margin-top: 12px; padding: 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #334155; }
            QPushButton { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 12px; color: #1e293b; font-weight: 600; }
            QPushButton:hover { background: #dbeafe; border-color: #60a5fa; }
            QPushButton:pressed { background: #bfdbfe; }
            QTableWidget, QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px; selection-background-color: #bfdbfe; selection-color: #0f172a; }
            QHeaderView::section { background: #eaf0f7; color: #334155; border: 0; border-bottom: 1px solid #cbd5e1; padding: 6px; font-weight: 600; }
            QStatusBar { background: #e2e8f0; color: #475569; }
        """)
        self.settings = load_settings()
        self.road_network = None
        self.selected_map_coordinate = None
        self.imported_intersection = None
        self.plan = SignalPlan.default()
        self.lane_turns = {}
        tabs = QTabWidget()
        tabs.addTab(self._build_map_tab(), "OpenStreetMap / 2ГИС")
        tabs.addTab(self._build_scheme_tab(), "Схема")
        tabs.addTab(self._build_signals_tab(), "Светофорная программа")
        tabs.addTab(self._build_demand_tab(), "Транспортный поток")
        tabs.addTab(self._build_detailed_tab(), "Интерактивная модель")
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
        self.city_presets = {
            "Москва": (55.751244, 37.618423),
            "Санкт-Петербург": (59.938630, 30.314130),
            "Новосибирск": (55.008353, 82.935733),
            "Кемерово": (55.354968, 86.087314),
            "Новокузнецк": (53.759635, 87.121570),
            "Красноярск": (56.015283, 92.893247),
            "Барнаул": (53.347997, 83.779806),
            "Томск": (56.484645, 84.947649),
            "Омск": (54.989342, 73.368212),
            "Екатеринбург": (56.838011, 60.597465),
            "Казань": (55.796127, 49.106405),
            "Нижний Новгород": (56.296504, 43.936059),
            "Самара": (53.195878, 50.100202),
            "Владивосток": (43.115536, 131.885485),
            "Другой город / координаты вручную": None,
        }
        self.city_combo = QComboBox()
        self.city_combo.addItems(self.city_presets.keys())
        self.city_combo.currentTextChanged.connect(self._apply_city_preset)
        self.lat = QDoubleSpinBox(); self.lat.setRange(-90,90); self.lat.setDecimals(6); self.lat.setValue(55.751244)
        self.lon = QDoubleSpinBox(); self.lon.setRange(-180,180); self.lon.setDecimals(6); self.lon.setValue(37.618423)
        self.radius = QSpinBox(); self.radius.setRange(100,50000); self.radius.setValue(1500); self.radius.setSuffix(" м")
        f.addRow("API-ключ", self.api_key); f.addRow("Поисковый запрос", self.query)
        f.addRow("Город для карты OSM", self.city_combo)
        f.addRow("Широта", self.lat); f.addRow("Долгота", self.lon); f.addRow("Радиус", self.radius)
        self._apply_city_preset(self.city_combo.currentText())
        l.addWidget(formbox)
        buttons = QHBoxLayout()
        save = QPushButton("Сохранить ключ локально"); save.clicked.connect(self._save_key); buttons.addWidget(save)
        search = QPushButton("Поиск объектов в 2ГИС"); search.clicked.connect(self._search_dgis); buttons.addWidget(search)
        l.addLayout(buttons)
        road_box = QGroupBox("Дорожная геометрия OpenStreetMap")
        road_layout = QVBoxLayout(road_box)
        road_note = QLabel("Выберите город из списка или задайте координаты вручную. Загружаются дороги только вокруг указанной точки; повторные запросы используют локальный кэш. Это геометрия дорог, а не готовая модель полос/фаз; перекрёсток и сигналы нужно проверить вручную.")
        road_note.setWordWrap(True); road_layout.addWidget(road_note)
        road_buttons = QHBoxLayout()
        osm = QPushButton("Импортировать дороги OSM вокруг точки"); osm.clicked.connect(self._import_osm_roads); road_buttons.addWidget(osm)
        auto_scheme = QPushButton("Создать схему по выбранному перекрёстку"); auto_scheme.clicked.connect(self._auto_create_intersection); road_buttons.addWidget(auto_scheme)
        geo = QPushButton("Открыть GeoJSON"); geo.clicked.connect(self._open_geojson); road_buttons.addWidget(geo)
        save_geo = QPushButton("Сохранить импорт GeoJSON"); save_geo.clicked.connect(self._save_geojson); road_buttons.addWidget(save_geo)
        road_layout.addLayout(road_buttons)
        self.osm_preview = OSMRoadPreview()
        self.osm_preview.coordinateSelected.connect(self._map_coordinate_selected)
        road_layout.addWidget(self.osm_preview, 1)
        map_controls = QHBoxLayout()
        zoom_in = QPushButton("Приблизить +")
        zoom_in.clicked.connect(lambda: self.osm_preview.zoom_by(1.25))
        map_controls.addWidget(zoom_in)
        zoom_out = QPushButton("Отдалить −")
        zoom_out.clicked.connect(lambda: self.osm_preview.zoom_by(1 / 1.25))
        map_controls.addWidget(zoom_out)
        reset_map = QPushButton("Вся сеть")
        reset_map.clicked.connect(self.osm_preview.reset_view)
        map_controls.addWidget(reset_map)
        map_controls.addStretch()
        road_layout.addLayout(map_controls)
        l.addWidget(road_box, 2)
        self.dgis_results = QTextEdit(); self.dgis_results.setReadOnly(True)
        self.dgis_results.setPlaceholderText("Здесь появятся результаты поиска 2ГИС или состояние импорта дорожных данных.")
        l.addWidget(self.dgis_results, 1)
        return w

    def _map_coordinate_selected(self, lat, lon, is_intersection):
        self.selected_map_coordinate = (float(lat), float(lon))
        self.lat.setValue(lat)
        self.lon.setValue(lon)
        self.city_combo.setCurrentText("Другой город / координаты вручную")
        label = "Выбран узел пересечения дорожных линий" if is_intersection else "Выбрана ближайшая точка дороги"
        self.dgis_results.setPlainText(
            f"{label}.\\nШирота: {lat:.7f}\\nДолгота: {lon:.7f}\\n"
            "Координаты сохранены. Нажмите «Создать схему по выбранному перекрёстку», чтобы автоматически оценить подходы и число полос."
        )
        self.statusBar().showMessage(f"{label}: {lat:.6f}, {lon:.6f}", 8000)

    def _auto_create_intersection(self):
        if not self.road_network or not self.road_network.get("features"):
            QMessageBox.information(self, "Нет дорожных данных",
                                    "Сначала импортируйте дороги OSM или откройте GeoJSON.")
            return
        if not self.selected_map_coordinate:
            QMessageBox.information(self, "Выберите перекрёсток",
                                    "На карте щёлкните по узлу пересечения дорог, затем повторите импорт.")
            return
        lat, lon = self.selected_map_coordinate
        try:
            inferred = infer_intersection_approaches(self.road_network, lat, lon)
        except RoadNetworkError as exc:
            QMessageBox.warning(self, "Не удалось создать схему", str(exc))
            return

        detected = inferred["approaches"]
        detected_count = inferred.get("detected_arm_count", inferred["detected_approach_count"])
        if detected_count == 3:
            self.template_combo.setCurrentText("Т-образный")
        elif detected_count == 5:
            self.template_combo.setCurrentText("5-конечный")
        elif detected_count >= 6:
            self.template_combo.setCurrentText("6-конечный")
        else:
            self.template_combo.setCurrentText("Классический 4-конечный")
        # The current schematic editor is four-sided. Missing approaches are kept
        # at one lane, and the generated metadata clearly marks them for manual review.
        for approach, spin in self.lane_spins.items():
            estimate = detected.get(approach, {}).get("lanes", 1)
            spin.setValue(max(1, min(8, int(estimate))))
        self.imported_intersection = inferred
        self._refresh_movement_rows()
        self.scheme.update()
        self.tabs.setCurrentWidget(self.tabs.widget(1))

        lines = [
            "Черновая схема создана по выбранной точке OpenStreetMap.",
            f"Координаты: {lat:.7f}, {lon:.7f}",
            f"Определено подходов: {inferred['detected_approach_count']} из 4",
            "",
            "Оценка полос по подходам:",
        ]
        for approach in self._active_approaches():
            details = detected.get(approach)
            if details:
                names = ", ".join(details.get("names", [])) or "дорога без названия"
                lines.append(f"• {approach}: {details['lanes']} полос(ы) (оценка); {names}")
            else:
                lines.append(f"• {approach}: дорога не обнаружена — схема условно оставлена с 1 полосой")
        lines += ["", "Проверьте вручную геометрию, число полос, разрешённые повороты, стоп-линии, пешеходные переходы и фазы."]
        lines += [f"• {warning}" for warning in inferred["warnings"]]
        self.dgis_results.setPlainText("\n".join(lines))
        self.statusBar().showMessage("Создан черновик схемы; проверьте его перед моделированием.", 10000)

    def _apply_city_preset(self, city_name):
        coords = self.city_presets.get(city_name)
        if coords is None:
            return
        lat, lon = coords
        self.lat.setValue(lat)
        self.lon.setValue(lon)

    def _import_osm_roads(self):
        from math import cos, radians
        from PySide6.QtCore import QThread, Signal

        lat, lon = self.lat.value(), self.lon.value()
        radius_km = min(self.radius.value() / 1000.0, 5.0)
        dlat = radius_km / 111.0
        dlon = radius_km / max(20.0, 111.0 * abs(cos(radians(lat))))
        bounds = (lat-dlat, lon-dlon, lat+dlat, lon+dlon)
        self.dgis_results.setPlainText(
            "Загружаются дороги выбранного участка OpenStreetMap… Окно программы останется доступным."
        )

        class OSMWorker(QThread):
            completed = Signal(object, object)

            def run(worker_self):
                try:
                    data = fetch_osm_roads(*bounds)
                    worker_self.completed.emit(data, None)
                except Exception as exc:
                    worker_self.completed.emit(None, str(exc))

        self.osm_worker = OSMWorker(self)
        self.osm_worker.completed.connect(self._finish_osm_import)
        self.osm_worker.start()

    def _finish_osm_import(self, data, error):
        if error:
            QMessageBox.warning(self, "Ошибка импорта дорожной сети", error)
            self.dgis_results.setPlainText(error)
            return

        self.road_network = data
        self.osm_preview.set_data(self.road_network)
        features = self.road_network.get("features", [])
        highways = {}
        for feature in features:
            kind = feature.get("properties", {}).get("highway", "unknown")
            highways[kind] = highways.get(kind, 0) + 1
        summary = "\n".join(f"  {kind}: {count}" for kind, count in sorted(highways.items()))
        cache_status = "локальный кэш" if data.get("cache_status") == "hit" else "загрузка из Overpass API"
        report = (f"Импортировано линейных объектов: {len(features)}\n"
                  f"Источник: OpenStreetMap ({cache_status})\n"
                  f"Сохранение кэша: {__import__('pathlib').Path.home() / '.traffic_signal_studio' / 'osm_cache'}\n\n"
                  f"Типы highway:\n{summary}\n\n"
                  "Ограничение: это линии дорог, не полосы движения, не направления разрешённых поворотов "
                  "и не контроллер светофора. Для моделирования потребуется очистка/конвертация и проверка геометрии.")
        self.dgis_results.setPlainText(report)

    def _open_geojson(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть дорожную сеть GeoJSON", "", "GeoJSON (*.geojson *.json)")
        if not path: return
        try:
            self.road_network = load_geojson(path)
            self.osm_preview.set_data(self.road_network)
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
        intro = QLabel("Выберите геометрический шаблон вручную или создайте черновик по выбранному узлу OSM на вкладке карты. Геометрия шаблона условная; число полос, направления и разрешённые повороты необходимо проверить.")
        intro.setWordWrap(True); layout.addWidget(intro)
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("Тип перекрёстка:"))
        self.template_combo = QComboBox()
        self.template_combo.addItems(list(IntersectionView.TEMPLATES.keys()))
        self.template_combo.currentTextChanged.connect(self._intersection_template_changed)
        template_row.addWidget(self.template_combo, 1)
        layout.addLayout(template_row)
        controls = QGroupBox("Полосы по направлениям"); form = QFormLayout(controls)
        self.lane_spins = {}
        self.lane_labels = {}
        self.lane_turns = {}
        for approach in IntersectionView.APPROACHES:
            spin = QSpinBox(); spin.setRange(1, 8); spin.setValue(2); spin.setSuffix(" полос(ы)")
            spin.valueChanged.connect(lambda value, name=approach: self._lane_count_changed(name, value))
            self.lane_spins[approach] = spin
            for lane in range(1, spin.value() + 1):
                self.lane_turns[(approach, lane)] = ("Прямо", "Налево", "Направо")
            form.addRow(approach, spin)
            self.lane_labels[approach] = form.labelForField(spin)
        layout.addWidget(controls)
        arrow_row = QHBoxLayout()
        arrows = QPushButton("Настроить стрелки по каждой полосе…")
        arrows.clicked.connect(self._edit_lane_arrows)
        arrow_row.addWidget(arrows)
        arrow_row.addStretch()
        layout.addLayout(arrow_row)
        self.scheme = IntersectionView()
        self.scheme.set_lane_turns(self.lane_turns)
        self._intersection_template_changed(self.template_combo.currentText())
        layout.addWidget(self.scheme, 1)
        buttons = QHBoxLayout()
        save = QPushButton("Сохранить схему…"); save.clicked.connect(self._save_intersection); buttons.addWidget(save)
        load = QPushButton("Загрузить схему…"); load.clicked.connect(self._load_intersection); buttons.addWidget(load)
        layout.addLayout(buttons)
        return w

    def _active_approaches(self):
        template = self.template_combo.currentText() if hasattr(self, "template_combo") else "Классический 4-конечный"
        return IntersectionView.TEMPLATE_APPROACHES.get(template, IntersectionView.TEMPLATE_APPROACHES["Классический 4-конечный"])

    def _intersection_template_changed(self, template):
        if hasattr(self, "scheme"):
            self.scheme.set_template(template)
        active = set(IntersectionView.TEMPLATE_APPROACHES.get(template, ()))
        for approach, spin in getattr(self, "lane_spins", {}).items():
            spin.setVisible(approach in active)
            label = getattr(self, "lane_labels", {}).get(approach)
            if label:
                label.setVisible(approach in active)
        if hasattr(self, "sign_approach_combo"):
            current = self.sign_approach_combo.currentText()
            self.sign_approach_combo.blockSignals(True)
            self.sign_approach_combo.clear()
            self.sign_approach_combo.addItems(list(self._active_approaches()))
            self.sign_approach_combo.setCurrentText(current if current in active else self._active_approaches()[0])
            self.sign_approach_combo.blockSignals(False)
        if hasattr(self, "movement_table"):
            self._refresh_movement_rows()
        if hasattr(self, "pedestrian_table"):
            previous = {self.pedestrian_table.item(r, 0).text(): self.pedestrian_table.item(r, 1).text()
                        for r in range(self.pedestrian_table.rowCount()) if self.pedestrian_table.item(r, 0)}
            self.pedestrian_table.setRowCount(len(self._active_approaches()))
            for row, name in enumerate(self._active_approaches()):
                self.pedestrian_table.setItem(row, 0, QTableWidgetItem(name))
                self.pedestrian_table.setItem(row, 1, QTableWidgetItem(previous.get(name, "80")))
        self.statusBar().showMessage(f"Выбран шаблон: {template}. Проверьте направления движения.", 6000)

    def _lane_count_changed(self, approach, count):
        for key in list(self.lane_turns):
            if key[0] == approach and key[1] > count:
                del self.lane_turns[key]
        for lane in range(1, count + 1):
            self.lane_turns.setdefault((approach, lane), ("Прямо", "Налево", "Направо"))
        if hasattr(self, "scheme"):
            self.scheme.set_lane_count(approach, count)
            self.scheme.set_lane_turns(self.lane_turns)
        if hasattr(self, "movement_table"):
            self._refresh_movement_rows()

    def _edit_lane_arrows(self):
        counts = {name: spin.value() for name, spin in self.lane_spins.items() if name in self._active_approaches()}
        dialog = LaneArrowDialog(counts, self.lane_turns, self)
        if dialog.exec():
            self.lane_turns = dialog.values()
            self.scheme.set_lane_turns(self.lane_turns)
            if hasattr(self, "movement_table"):
                self._refresh_movement_rows()
            self.statusBar().showMessage("Разрешённые манёвры по полосам обновлены.", 5000)

    def _edit_phase_movements(self):
        row = self.phase_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Выберите фазу", "Выберите строку фазы, которой назначаются движения.")
            return
        try:
            phases = self._read_phase_table()
            movements, pedestrians = self._read_detailed_demands()
        except (ValueError, AttributeError) as exc:
            QMessageBox.warning(self, "Не удалось открыть назначения", str(exc))
            return
        selected = set(phases[row].movements)
        all_ids = [m.movement_id for m in movements] + [p.movement_id for p in pedestrians]
        for token in list(selected):
            if token.endswith(":*"):
                selected.update(key for key in all_ids if key.startswith(token[:-1]))
                selected.discard(token)
        dialog = PhaseMovementDialog(movements, pedestrians, selected, phases[row].name, self)
        if dialog.exec():
            self.phase_table.item(row, 6).setText(", ".join(dialog.selected_ids()))
            self._apply_phases()

    def _build_signals_tab(self):
        w = QWidget(); layout = QVBoxLayout(w)
        note = QLabel("Редактируйте любое количество фаз. Время красного и зелёного мигающего включено в расчётную длину цикла. Это редактор плана, а не проверка конфликтов сигналов по ГОСТ.")
        note.setWordWrap(True); layout.addWidget(note)
        self.phase_table = QTableWidget(0, 7)
        self.phase_table.setHorizontalHeaderLabels(["Название фазы", "Зелёный, с", "Зелёный мигающий, с", "Жёлтый, с", "Красный, с", "Межфазный, с", "Назначенные движения (список)"])
        self.phase_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.phase_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.phase_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.phase_table, 1)
        buttons = QHBoxLayout()
        add = QPushButton("Добавить фазу"); add.clicked.connect(self._add_phase); buttons.addWidget(add)
        remove = QPushButton("Удалить выбранную фазу"); remove.clicked.connect(self._remove_phase); buttons.addWidget(remove)
        assign = QPushButton("Назначить движения выбранной фазе…"); assign.clicked.connect(self._edit_phase_movements); buttons.addWidget(assign)
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
                      phase.yellow_seconds, phase.red_seconds, phase.intergreen_seconds,
                      ", ".join(phase.movements)]
            for c, value in enumerate(values):
                self.phase_table.setItem(r, c, QTableWidgetItem(str(value)))

    def _add_phase(self):
        row = self.phase_table.rowCount()
        if row >= 32:
            QMessageBox.information(self, "Лимит фаз", "Можно добавить не более 32 фаз."); return
        self.phase_table.insertRow(row)
        for col, value in enumerate([f"Фаза {row + 1}", 20, 3, 3, 0, 2, ""]):
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
            cells = [self.phase_table.item(r, c) for c in range(7)]
            if any(cell is None for cell in cells):
                raise ValueError(f"Фаза {r + 1}: заполните все поля.")
            try:
                movements = [token.strip() for token in cells[6].text().split(",") if token.strip()]
                phases.append(Phase(cells[0].text().strip(), int(cells[1].text()), int(cells[3].text()),
                                    int(cells[5].text()), int(cells[2].text()), int(cells[4].text()), movements))
            except ValueError as exc:
                raise ValueError(f"Фаза {r + 1}: времена должны быть целыми числами.") from exc
        return phases

    def _apply_phases(self, show_error=True):
        try:
            self.plan = SignalPlan(self._read_phase_table())
            assigned_count = sum(len(phase.movements) for phase in self.plan.phases)
            suffix = f"Назначений: {assigned_count}" if assigned_count else "ВНИМАНИЕ: движения не назначены — транспорт не получит зелёный"
            self.phase_summary.setText(f"Фаз: {len(self.plan.phases)} · Цикл: {self.plan.cycle_seconds} с · {suffix}")
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
        data = {
            "schema_version": 3,
            "lane_counts": {k: v.value() for k, v in self.lane_spins.items()},
            "source_intersection": self.imported_intersection,
            "lane_turns": [
                {"approach": approach, "lane": lane, "turns": list(turns)}
                for (approach, lane), turns in sorted(self.lane_turns.items())
            ],
        }
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
            version = data.get("schema_version", 1)
            if version not in (1, 2, 3):
                raise ValueError("Неизвестная версия схемы перекрёстка.")
            counts = data["lane_counts"]
            self.imported_intersection = data.get("source_intersection") if version >= 3 else None
            for approach, spin in self.lane_spins.items():
                spin.setValue(int(counts.get(approach, 2)))
            if version == 2:
                restored = {}
                for item in data.get("lane_turns", []):
                    approach = item["approach"]
                    lane = int(item["lane"])
                    turns = tuple(item["turns"])
                    if approach not in self.lane_spins or lane < 1 or lane > self.lane_spins[approach].value():
                        continue
                    if not turns or any(turn not in ("Прямо", "Налево", "Направо") for turn in turns):
                        raise ValueError(f"Некорректные манёвры для полосы {approach} {lane}.")
                    restored[(approach, lane)] = tuple(dict.fromkeys(turns))
                for approach, spin in self.lane_spins.items():
                    for lane in range(1, spin.value() + 1):
                        restored.setdefault((approach, lane), ("Прямо", "Налево", "Направо"))
                self.lane_turns = restored
                self.scheme.set_lane_turns(self.lane_turns)
                self._refresh_movement_rows()
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

    def _build_detailed_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        intro = QLabel(
            "Интерактивная модель: задайте интенсивность отдельно для каждой полосы и манёвра, "
            "а также для каждого пешеходного перехода. Назначения фаз редактируются на вкладке "
            "«Светофорная программа»: используйте идентификаторы вроде Север:* или Пешеходы:Север. "
            "Звёздочка назначает все полосы соответствующего подхода. Схема анимирована и условна."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)
        sign_box = QGroupBox("Дорожные знаки (ПДД РФ)")
        sign_layout = QHBoxLayout(sign_box)
        self.sign_approach_combo = QComboBox()
        self.sign_approach_combo.addItems(list(self._active_approaches()))
        self.sign_code_combo = QComboBox()
        for code, (name, group) in SIGN_CATALOG.items():
            self.sign_code_combo.addItem(f"{code} — {name}", code)
        add_sign = QPushButton("Добавить знак")
        add_sign.clicked.connect(self._add_road_sign)
        remove_sign = QPushButton("Убрать знаки с подхода")
        remove_sign.clicked.connect(self._remove_road_signs)
        sign_layout.addWidget(QLabel("Подход:"))
        sign_layout.addWidget(self.sign_approach_combo)
        sign_layout.addWidget(QLabel("Знак:"))
        sign_layout.addWidget(self.sign_code_combo, 2)
        sign_layout.addWidget(add_sign)
        sign_layout.addWidget(remove_sign)
        outer.addWidget(sign_box)
        self.road_signs_label = QLabel("Установленные знаки: нет")
        self.road_signs_label.setWordWrap(True)
        outer.addWidget(self.road_signs_label)

        split = QHBoxLayout()
        tables = QVBoxLayout()
        move_box = QGroupBox("Транспортный спрос — авт./ч")
        move_layout = QVBoxLayout(move_box)
        self.movement_table = QTableWidget(0, 4)
        self.movement_table.setHorizontalHeaderLabels(["Подход", "Полоса", "Манёвр", "Авт./ч"])
        self.movement_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        move_layout.addWidget(self.movement_table)
        refresh = QPushButton("Сформировать строки по числу полос")
        refresh.clicked.connect(self._refresh_movement_rows)
        move_layout.addWidget(refresh)
        tables.addWidget(move_box, 3)
        ped_box = QGroupBox("Пешеходные переходы — чел./ч")
        ped_layout = QVBoxLayout(ped_box)
        self.pedestrian_table = QTableWidget(len(self._active_approaches()), 2)
        self.pedestrian_table.setHorizontalHeaderLabels(["Переход", "Чел./ч"])
        self.pedestrian_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for row, name in enumerate(self._active_approaches()):
            self.pedestrian_table.setItem(row, 0, QTableWidgetItem(name))
            self.pedestrian_table.setItem(row, 1, QTableWidgetItem("80"))
        ped_layout.addWidget(self.pedestrian_table)
        tables.addWidget(ped_box, 1)
        split.addLayout(tables, 3)
        self.traffic_scene = TrafficScene()
        self.traffic_scene.setToolTip("Щёлкните правой кнопкой мыши за пределами перекрёстка, чтобы установить дорожный знак на подходе.")
        split.addWidget(self.traffic_scene, 2)
        outer.addLayout(split, 1)
        controls = QHBoxLayout()
        self.detail_duration = QSpinBox()
        self.detail_duration.setRange(60, 7200)
        self.detail_duration.setValue(1800)
        self.detail_duration.setSuffix(" с")
        self.detail_saturation = QSpinBox()
        self.detail_saturation.setRange(300, 3600)
        self.detail_saturation.setValue(1800)
        self.detail_saturation.setSuffix(" авт./ч/полосу")
        self.detail_seed = QSpinBox()
        self.detail_seed.setRange(1, 999999)
        self.detail_seed.setValue(42)
        controls.addWidget(QLabel("Длительность:"))
        controls.addWidget(self.detail_duration)
        controls.addWidget(QLabel("Насыщенный поток:"))
        controls.addWidget(self.detail_saturation)
        controls.addWidget(QLabel("Seed:"))
        controls.addWidget(self.detail_seed)
        run = QPushButton("Рассчитать сценарий")
        run.clicked.connect(self._run_detailed)
        controls.addWidget(run)
        start = QPushButton("▶ Запуск анимации")
        start.clicked.connect(self.traffic_scene.start)
        controls.addWidget(start)
        self.speed_button = QPushButton("Скорость: 1×")
        self.speed_button.setToolTip("Переключить скорость анимации между обычной и двойной")
        self.speed_button.clicked.connect(self._toggle_animation_speed)
        controls.addWidget(self.speed_button)
        pause = QPushButton("Ⅱ Пауза")
        pause.clicked.connect(self.traffic_scene.pause)
        controls.addWidget(pause)
        reset = QPushButton("↺ Сброс")
        reset.clicked.connect(self.traffic_scene.reset)
        controls.addWidget(reset)
        outer.addLayout(controls)
        self.detailed_results = QTextEdit()
        self.detailed_results.setReadOnly(True)
        self.detailed_results.setMaximumHeight(130)
        self.detailed_results.setPlaceholderText("Здесь появится отчёт по транспорту и пешеходам.")
        outer.addWidget(self.detailed_results)
        self._refresh_movement_rows()
        return w

    def _add_road_sign(self):
        approach = self.sign_approach_combo.currentText()
        code = self.sign_code_combo.currentData()
        if not code:
            return
        self.traffic_scene.road_signs = [
            sign for sign in self.traffic_scene.road_signs
            if not (sign.get("approach") == approach and sign.get("code") == code)
        ]
        self.traffic_scene.road_signs.append({
            "approach": approach, "code": code, "name": SIGN_CATALOG[code][0]
        })
        self.traffic_scene.update()
        self._refresh_road_signs_label()
        self.statusBar().showMessage(f"Знак {code} установлен на подходе «{approach}».", 5000)

    def _remove_road_signs(self):
        approach = self.sign_approach_combo.currentText()
        self.traffic_scene.road_signs = [
            sign for sign in self.traffic_scene.road_signs
            if sign.get("approach") != approach
        ]
        self.traffic_scene.update()
        self._refresh_road_signs_label()
        self.statusBar().showMessage(f"Знаки с подхода «{approach}» убраны.", 5000)

    def _refresh_road_signs_label(self):
        signs = self.traffic_scene.road_signs
        if not signs:
            self.road_signs_label.setText("Установленные знаки: нет")
            return
        entries = [
            f"{sign.get('approach')}: {sign.get('code')} — {sign.get('name')}"
            for sign in signs
        ]
        self.road_signs_label.setText("Установленные знаки: " + "; ".join(entries))

    def _refresh_movement_rows(self):
        previous = {}
        if hasattr(self, "movement_table"):
            for row in range(self.movement_table.rowCount()):
                try:
                    key = (self.movement_table.item(row, 0).text(),
                           int(self.movement_table.item(row, 1).text()),
                           self.movement_table.item(row, 2).text())
                    previous[key] = int(self.movement_table.item(row, 3).text())
                except (AttributeError, ValueError):
                    pass
        rows = []
        turns = ("Прямо", "Налево", "Направо")
        for approach in self._active_approaches():
            count = self.lane_spins[approach].value()
            for lane in range(1, count + 1):
                for turn in self.lane_turns.get((approach, lane), turns):
                    rows.append((approach, lane, turn, previous.get((approach, lane, turn), 180)))
        self.movement_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for col, value in enumerate(values):
                self.movement_table.setItem(row, col, QTableWidgetItem(str(value)))

    def _read_detailed_demands(self):
        movements = []
        for row in range(self.movement_table.rowCount()):
            try:
                approach = self.movement_table.item(row, 0).text().strip()
                lane = int(self.movement_table.item(row, 1).text())
                turn = self.movement_table.item(row, 2).text().strip()
                demand = int(self.movement_table.item(row, 3).text())
            except (AttributeError, ValueError) as exc:
                raise ValueError(f"Транспортная строка {row + 1}: проверьте полосу и интенсивность.") from exc
            if approach not in self._active_approaches() or turn not in ("Прямо", "Налево", "Направо"):
                raise ValueError(f"Транспортная строка {row + 1}: неизвестный подход или манёвр.")
            if lane < 1 or demand < 0:
                raise ValueError(f"Транспортная строка {row + 1}: полоса должна быть положительной, интенсивность — неотрицательной.")
            movements.append(MovementDemand(approach, lane, turn, demand))
        pedestrians = []
        for row in range(self.pedestrian_table.rowCount()):
            try:
                crossing = self.pedestrian_table.item(row, 0).text().strip()
                demand = int(self.pedestrian_table.item(row, 1).text())
            except (AttributeError, ValueError) as exc:
                raise ValueError(f"Пешеходная строка {row + 1}: введите целую интенсивность.") from exc
            if crossing not in self._active_approaches() or demand < 0:
                raise ValueError(f"Пешеходная строка {row + 1}: проверьте название перехода и интенсивность.")
            pedestrians.append(PedestrianDemand(crossing, demand))
        return movements, pedestrians

    def _toggle_animation_speed(self):
        multiplier = 2 if self.traffic_scene.speed_multiplier == 1 else 1
        self.traffic_scene.set_speed(multiplier)
        self.speed_button.setText(f"Скорость: {multiplier}×")

    def _run_detailed(self):
        if not self._apply_phases():
            return
        try:
            movements, pedestrians = self._read_detailed_demands()
            result = simulate_detailed(
                movements, pedestrians, self.plan.phases,
                self.detail_duration.value(), self.detail_saturation.value(),
                self.detail_seed.value(), signs=self.traffic_scene.road_signs
            )
        except (ValueError, ArithmeticError) as exc:
            QMessageBox.warning(self, "Ошибка детального сценария", str(exc))
            return
        self.traffic_scene.configure(
            self.plan.phases, movements, pedestrians, self.plan.cycle_seconds,
            {name: spin.value() for name, spin in self.lane_spins.items() if name in self._active_approaches()},\n            {key: value for key, value in self.lane_turns.items() if key[0] in self._active_approaches()},
            result=result, signs=self.traffic_scene.road_signs
        )
        self.detailed_results.setPlainText(result.report())

    def _build_results_tab(self):
        w = QWidget(); l = QVBoxLayout(w); self.results_text = QTextEdit(); self.results_text.setReadOnly(True)
        self.results_text.setPlaceholderText("Запустите моделирование на вкладке «Транспортный поток».")
        l.addWidget(self.results_text); return w

    def _run(self):
        if not self._apply_phases():
            return
        cfg = SimulationConfig(
            self.ns.value(), self.ew.value(), self.heavy.value(),
            self.duration.value(), self.saturation.value(), self.seed.value()
        )
        try:
            result = run_simulation(cfg, self.plan)
        except (ValueError, ArithmeticError) as exc:
            QMessageBox.warning(self, "Ошибка моделирования", str(exc))
            return
        self.results_text.setPlainText(result.to_report())
        self.tabs.setCurrentWidget(self.results_text.parentWidget())
