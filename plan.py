"""Traffic signal phase plan and JSON persistence."""
from dataclasses import dataclass, asdict
import json
from pathlib import Path


@dataclass
class Phase:
    name: str
    green_seconds: int
    yellow_seconds: int
    intergreen_seconds: int
    flashing_green_seconds: int = 0
    red_seconds: int = 0

    @property
    def duration_seconds(self):
        return (self.green_seconds + self.flashing_green_seconds + self.yellow_seconds
                + self.red_seconds + self.intergreen_seconds)


class SignalPlan:
    def __init__(self, phases):
        self.phases = list(phases)
        self.validate()

    def validate(self):
        if len(self.phases) < 1:
            raise ValueError("Добавьте хотя бы одну фазу.")
        if len(self.phases) > 32:
            raise ValueError("Допускается не более 32 фаз в одном плане.")
        for i, phase in enumerate(self.phases, 1):
            if not phase.name.strip():
                raise ValueError(f"Фаза {i}: укажите название.")
            for label, value, minimum in (
                ("зелёный", phase.green_seconds, 5),
                ("зелёный мигающий", phase.flashing_green_seconds, 0),
                ("жёлтый", phase.yellow_seconds, 0),
                ("красный", phase.red_seconds, 0),
                ("межфазный интервал", phase.intergreen_seconds, 0),
            ):
                if not isinstance(value, int) or value < minimum:
                    raise ValueError(f"Фаза {i}: время «{label}» должно быть целым числом не менее {minimum} с.")
        if self.cycle_seconds > 3600:
            raise ValueError("Длина цикла превышает 3600 с; проверьте введённые интервалы.")

    @classmethod
    def default(cls):
        return cls([
            Phase("Север—Юг", 35, 3, 2, 3, 0),
            Phase("Восток—Запад", 30, 3, 2, 3, 0),
        ])

    @property
    def cycle_seconds(self):
        return sum(p.duration_seconds for p in self.phases)

    def to_dict(self):
        return {"schema_version": 2, "phases": [asdict(p) for p in self.phases]}

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            version = data.get("schema_version")
            if version not in (1, 2):
                raise ValueError("Неизвестная версия формата плана.")
            phases = []
            for item in data["phases"]:
                if version == 1:
                    item = {"name": item["name"], "green_seconds": item["green_seconds"],
                            "flashing_green_seconds": 0, "yellow_seconds": item["yellow_seconds"],
                            "red_seconds": 0, "intergreen_seconds": item["intergreen_seconds"]}
                else:
                    item.setdefault("flashing_green_seconds", 0)
                    item.setdefault("red_seconds", 0)
                phases.append(Phase(**item))
            return cls(phases)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"Не удалось загрузить план: {exc}") from exc
