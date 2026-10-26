"""Lane-level demand and pedestrian queue model for interactive intersection scenarios.

This is a transparent educational model: arrivals are stochastic, service is capacity-based,
and only movements explicitly assigned to a phase receive green.
"""
from dataclasses import dataclass
import random


@dataclass(frozen=True)
class MovementDemand:
    approach: str
    lane: int
    turn: str
    vehicles_per_hour: int

    @property
    def movement_id(self):
        return f"{self.approach}:{self.lane}:{self.turn}"


@dataclass(frozen=True)
class PedestrianDemand:
    crossing: str
    pedestrians_per_hour: int

    @property
    def movement_id(self):
        return f"Пешеходы:{self.crossing}"


@dataclass
class DetailedResult:
    duration_seconds: int
    arrivals: int
    served: int
    max_queue: int
    final_queue: int
    queue_person_seconds: float
    pedestrian_arrivals: int
    pedestrians_served: int
    pedestrian_max_queue: int
    pedestrian_final_queue: int
    events: list = None

    @property
    def average_vehicle_delay(self):
        return self.queue_person_seconds / self.served if self.served else 0.0

    def report(self):
        return (
            "ДЕТАЛЬНЫЙ СЦЕНАРИЙ ПЕРЕКРЁСТКА\n\n"
            f"Продолжительность: {self.duration_seconds} с\n"
            f"Транспорт: прибыло {self.arrivals}, обслужено {self.served}, "
            f"максимальная суммарная очередь {self.max_queue}, остаток {self.final_queue}\n"
            f"Пешеходы: прибыло {self.pedestrian_arrivals}, прошло {self.pedestrians_served}, "
            f"максимальная очередь {self.pedestrian_max_queue}, остаток {self.pedestrian_final_queue}\n"
            f"Оценочная задержка на обслуженный автомобиль: {self.average_vehicle_delay:.1f} с\n\n"
            "Модель учебная: пуассоновские прибытия и упрощённая пропускная способность; "
            "нет перестроений, геометрических траекторий, конфликтных точек и калибровки по наблюдениям."
        )


def simulate_detailed(movements, pedestrians, phases, duration_seconds=1800,
                      saturation_vph=1800, seed=42):
    if duration_seconds < 1 or saturation_vph < 1:
        raise ValueError("Длительность и насыщенная интенсивность должны быть положительными.")
    if not movements and not pedestrians:
        raise ValueError("Добавьте хотя бы один транспортный поток или пешеходный переход.")
    for item in movements:
        if item.vehicles_per_hour < 0 or item.lane < 1:
            raise ValueError(f"Некорректный спрос для полосы {item.movement_id}.")
    for item in pedestrians:
        if item.pedestrians_per_hour < 0:
            raise ValueError(f"Интенсивность пешеходов не может быть отрицательной: {item.crossing}.")
    if not phases:
        raise ValueError("Добавьте хотя бы одну фазу.")
    rng = random.Random(seed)
    cycle = sum(p.duration_seconds for p in phases)
    if cycle <= 0:
        raise ValueError("Длина цикла должна быть положительной.")
    # Empty phase assignments deliberately serve no movement: a missing signal group
    # must be visible and corrected by the user rather than silently guessed by the model.
    phases_by_start = []
    cursor = 0
    all_ids = [m.movement_id for m in movements] + [p.movement_id for p in pedestrians]
    for phase in phases:
        green = phase.green_seconds + phase.flashing_green_seconds
        assigned = set()
        for token in phase.movements:
            if token.endswith(":*"):
                prefix = token[:-1]
                assigned.update(key for key in all_ids if key.startswith(prefix))
            else:
                assigned.add(token)
        phases_by_start.append((cursor, cursor + green, assigned))
        cursor += phase.duration_seconds
    queues = {m.movement_id: 0 for m in movements}
    pqueues = {p.movement_id: 0 for p in pedestrians}
    max_queue = max_pqueue = arrivals = served = parrivals = pserved = 0
    queue_seconds = 0.0
    events = []
    credits = {key: 0.0 for key in queues}
    pcredits = {key: 0.0 for key in pqueues}
    movement_map = {m.movement_id: m for m in movements}
    pedestrian_map = {p.movement_id: p for p in pedestrians}
    for t in range(duration_seconds):
        for key, demand in movement_map.items():
            if rng.random() < demand.vehicles_per_hour / 3600.0:
                queues[key] += 1
                arrivals += 1
                events.append({"time": t, "kind": "vehicle_arrival", "movement_id": key})
        for key, demand in pedestrian_map.items():
            if rng.random() < demand.pedestrians_per_hour / 3600.0:
                pqueues[key] += 1
                parrivals += 1
                events.append({"time": t, "kind": "pedestrian_arrival", "movement_id": key})
        pos = t % cycle
        active = set()
        for start, end, assigned in phases_by_start:
            if start <= pos < end:
                active = assigned
                break
        for key, demand in movement_map.items():
            if key in active:
                credits[key] += saturation_vph / 3600.0
                if queues[key] and credits[key] >= 1.0:
                    queues[key] -= 1
                    served += 1
                    events.append({"time": t, "kind": "vehicle_served", "movement_id": key})
                    credits[key] -= 1.0
        for key, demand in pedestrian_map.items():
            if key in active:
                pcredits[key] += 1.2 / 1.0  # simplified pedestrian discharge: about 1.2 people/s
                if pqueues[key] and pcredits[key] >= 1.0:
                    pqueues[key] -= 1
                    pserved += 1
                    events.append({"time": t, "kind": "pedestrian_served", "movement_id": key})
                    pcredits[key] -= 1.0
        max_queue = max(max_queue, sum(queues.values()))
        max_pqueue = max(max_pqueue, sum(pqueues.values()))
        queue_seconds += sum(queues.values())
    return DetailedResult(duration_seconds, arrivals, served, max_queue,
                          sum(queues.values()), queue_seconds, parrivals, pserved,
                          max_pqueue, sum(pqueues.values()), events)
