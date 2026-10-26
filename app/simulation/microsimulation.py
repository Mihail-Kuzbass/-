"""Microscopic vehicle movement model for signalized intersections.

This module is deliberately separate from the legacy queue-only model. It provides
per-vehicle speed, acceleration, braking, headway and conflict-zone occupancy.
It is a simplified engineering model, not a validated crash-prediction package.
"""
from dataclasses import dataclass, field
import random

from app.simulation.detailed import MovementDemand, vehicle_movements_conflict


@dataclass(frozen=True)
class VehicleProfile:
    """User-editable vehicle dynamics; speed is entered in km/h."""
    name: str
    desired_speed_kmh: float
    length_m: float
    width_m: float
    max_acceleration_mps2: float
    comfortable_deceleration_mps2: float
    reaction_time_s: float = 1.0
    min_gap_m: float = 2.0
    weight: float = 1.0

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Укажите название типа транспорта.")
        if not 1 <= self.desired_speed_kmh <= 160:
            raise ValueError("Целевая скорость должна быть в диапазоне 1–160 км/ч.")
        for label, value in (
            ("длина", self.length_m), ("ширина", self.width_m),
            ("ускорение", self.max_acceleration_mps2),
            ("замедление", self.comfortable_deceleration_mps2),
            ("время реакции", self.reaction_time_s),
            ("минимальный интервал", self.min_gap_m), ("вес потока", self.weight),
        ):
            if value <= 0:
                raise ValueError(f"Параметр «{label}» должен быть положительным.")


DEFAULT_VEHICLE_PROFILES = (
    VehicleProfile("Легковой автомобиль", 50, 4.5, 1.8, 2.0, 3.5, 1.0, 2.0, 0.72),
    VehicleProfile("Автобус", 40, 11.0, 2.5, 0.8, 2.5, 1.3, 3.0, 0.08),
    VehicleProfile("Грузовой автомобиль", 40, 8.0, 2.5, 0.7, 2.5, 1.3, 3.0, 0.15),
    VehicleProfile("Мотоцикл", 50, 2.2, 0.9, 3.0, 4.0, 0.8, 1.5, 0.05),
)


@dataclass(frozen=True)
class MicroMovement:
    """Movement demand and optional speed override for a particular approach/lane/turn."""
    approach: str
    lane: int
    turn: str
    vehicles_per_hour: int
    speed_limit_kmh: float = 50.0

    @property
    def movement_id(self):
        return f"{self.approach}:{self.lane}:{self.turn}"

    def as_demand(self):
        return MovementDemand(self.approach, self.lane, self.turn, self.vehicles_per_hour)


@dataclass
class Vehicle:
    vehicle_id: int
    movement_id: str
    profile: VehicleProfile
    distance_to_stop_m: float
    speed_mps: float = 0.0
    state: str = "approach"
    crossing_remaining_s: float = 0.0
    total_delay_s: float = 0.0


@dataclass
class MicroSimulationResult:
    duration_seconds: int
    arrivals: int
    departed: int
    final_queue: int
    max_queue: int
    average_speed_kmh: float
    average_delay_s: float
    stopped_vehicle_seconds: float
    events: list = field(default_factory=list)

    def report(self):
        return (
            "МИКРОМОДЕЛИРОВАНИЕ ДВИЖЕНИЯ\n"
            f"Длительность: {self.duration_seconds} с\n"
            f"Прибыло: {self.arrivals}; проехало перекрёсток: {self.departed}\n"
            f"Очередь в конце: {self.final_queue}; максимальная очередь: {self.max_queue}\n"
            f"Средняя скорость движущихся автомобилей: {self.average_speed_kmh:.1f} км/ч\n"
            f"Средняя задержка выехавших автомобилей: {self.average_delay_s:.1f} с\n"
            f"Время остановки всех автомобилей суммарно: {self.stopped_vehicle_seconds:.1f} авт·с\n"
            "Примечание: геометрия траекторий и ДТП не рассчитываются этим этапом; "
            "конфликты манёвров разрешаются по упрощённой схеме перекрёстка."
        )


def _phase_movements(phases, all_ids):
    """Build cycle intervals from phase assignments; empty assignments serve nothing."""
    intervals, cursor = [], 0
    for phase in phases:
        assigned = set()
        for token in phase.movements:
            if token.endswith(":*"):
                assigned.update(mid for mid in all_ids if mid.startswith(token[:-1]))
            else:
                assigned.add(token)
        # Yellow, flashing green, all-red and intergreen do not admit new entries.
        green_end = cursor + max(0, phase.green_seconds)
        intervals.append((cursor, green_end, assigned))
        cursor += phase.duration_seconds
    if cursor <= 0:
        raise ValueError("Длина цикла должна быть положительной.")
    return cursor, intervals


def simulate_micro(movements, phases, duration_seconds=1800, profiles=None,
                   seed=42, approach_length_m=100.0, intersection_length_m=18.0,
                   time_step_s=0.2):
    """Run a reproducible per-vehicle simulation with car-following and conflict gating.

    Speeds are user-entered in km/h and converted to m/s internally. Arrivals follow
    independent Poisson-like Bernoulli processes. Conflict zones are exclusive resources;
    a conflicting movement waits until the current vehicle clears the junction.
    """
    if duration_seconds < 1:
        raise ValueError("Длительность моделирования должна быть положительной.")
    if not 0.05 <= time_step_s <= 1.0:
        raise ValueError("Шаг моделирования должен быть в диапазоне 0,05–1,0 с.")
    if approach_length_m <= 0 or intersection_length_m <= 0:
        raise ValueError("Длины подхода и перекрёстка должны быть положительными.")
    if not movements or not phases:
        raise ValueError("Добавьте транспортные потоки и хотя бы одну фазу.")
    for item in movements:
        if item.vehicles_per_hour < 0 or item.lane < 1 or not 1 <= item.speed_limit_kmh <= 160:
            raise ValueError(f"Некорректные параметры потока {item.movement_id}.")
    profiles = tuple(profiles or DEFAULT_VEHICLE_PROFILES)
    if not profiles or sum(p.weight for p in profiles) <= 0:
        raise ValueError("Нужен хотя бы один тип транспорта с положительным весом потока.")

    rng = random.Random(seed)
    movement_map = {m.movement_id: m for m in movements}
    if len(movement_map) != len(movements):
        raise ValueError("Идентификаторы движений должны быть уникальными.")
    cycle, intervals = _phase_movements(phases, set(movement_map))
    demands = [m.as_demand() for m in movements]
    conflicts = {
        mid: {other for other in movement_map if other != mid and
              vehicle_movements_conflict(movement_map[mid].as_demand(),
                                          movement_map[other].as_demand())}
        for mid in movement_map
    }
    queues = {mid: [] for mid in movement_map}
    active = []
    events = []
    next_id = arrivals = departed = max_queue = 0
    stopped_vehicle_seconds = 0.0
    speed_samples = []
    delay_samples = []
    occupied = {}  # movement_id -> seconds remaining until its vehicle clears the junction
    elapsed = 0.0
    steps = int(duration_seconds / time_step_s)

    def choose_profile():
        return rng.choices(profiles, weights=[p.weight for p in profiles], k=1)[0]

    for step in range(steps):
        t = step * time_step_s
        # Generate arrivals at the requested hourly intensity.
        for movement in movements:
            if rng.random() < movement.vehicles_per_hour * time_step_s / 3600.0:
                profile = choose_profile()
                vehicle = Vehicle(next_id, movement.movement_id, profile,
                                  approach_length_m, 0.0)
                next_id += 1
                arrivals += 1
                queues[movement.movement_id].append(vehicle)
                events.append({"time": round(t, 2), "kind": "arrival",
                               "vehicle_id": vehicle.vehicle_id,
                               "movement_id": vehicle.movement_id,
                               "vehicle_type": profile.name})
        # Move vehicles already inside the junction and release conflict zones.
        for vehicle in list(active):
            vehicle.crossing_remaining_s -= time_step_s
            if vehicle.crossing_remaining_s <= 0:
                active.remove(vehicle)
                occupied.pop(vehicle.movement_id, None)
                departed += 1
                delay_samples.append(vehicle.total_delay_s)
                events.append({"time": round(t, 2), "kind": "departed",
                               "vehicle_id": vehicle.vehicle_id,
                               "movement_id": vehicle.movement_id})
        for mid in list(occupied):
            occupied[mid] = max(0.0, occupied[mid] - time_step_s)
            if occupied[mid] <= 0:
                occupied.pop(mid, None)

        pos = t % cycle
        green_ids = set()
        for start, end, assigned in intervals:
            if start <= pos < end:
                green_ids = assigned
                break
        # Movement-level deterministic arbitration prevents simultaneous conflicting entry.
        entry_candidates = []
        for mid, queue in queues.items():
            if not queue or mid not in green_ids:
                continue
            if any(other in occupied for other in conflicts[mid]):
                continue
            entry_candidates.append((queue[0].distance_to_stop_m, mid, queue[0]))
        entry_candidates.sort(key=lambda item: (item[0], item[1]))
        admitted = set()
        for _, mid, vehicle in entry_candidates:
            if mid in occupied or any(other in admitted for other in conflicts[mid]):
                continue
            if vehicle.distance_to_stop_m <= max(
                vehicle.profile.length_m + 2.0,
                vehicle.speed_mps * vehicle.profile.reaction_time_s
                + vehicle.speed_mps ** 2 / (2 * vehicle.profile.comfortable_deceleration_mps2)
            ):
                admitted.add(mid)

        # Car-following: free-flow acceleration plus safe stopping at the stop line.
        for mid, queue in queues.items():
            movement = movement_map[mid]
            leader_distance = None
            for index, vehicle in enumerate(queue):
                target = min(vehicle.profile.desired_speed_kmh, movement.speed_limit_kmh) / 3.6
                gap = (vehicle.distance_to_stop_m if index == 0
                       else queue[index - 1].distance_to_stop_m - vehicle.distance_to_stop_m
                       - queue[index - 1].profile.length_m)
                if index > 0:
                    leader = queue[index - 1]
                    safe_gap = vehicle.profile.min_gap_m + vehicle.speed_mps * vehicle.profile.reaction_time_s
                    if gap <= safe_gap:
                        target = min(target, max(0.0, leader.speed_mps - 0.5))
                is_first = index == 0
                may_enter = is_first and mid in admitted
                stop_distance = max(0.0, vehicle.distance_to_stop_m - vehicle.profile.length_m / 2)
                if is_first and not may_enter:
                    safe_speed = (2 * vehicle.profile.comfortable_deceleration_mps2 *
                                  max(0.0, stop_distance - 1.0)) ** 0.5
                    target = min(target, safe_speed)
                acceleration = vehicle.profile.max_acceleration_mps2
                vehicle.speed_mps += max(-vehicle.profile.comfortable_deceleration_mps2,
                                          min(acceleration, (target - vehicle.speed_mps) / time_step_s)) * time_step_s
                vehicle.speed_mps = max(0.0, min(vehicle.speed_mps, target))
                movement_distance = vehicle.speed_mps * time_step_s
                if may_enter:
                    queue.pop(0)
                    vehicle.state = "crossing"
                    vehicle.crossing_remaining_s = max(
                        1.0, (intersection_length_m + vehicle.profile.length_m) /
                        max(2.0, min(target, 8.0))
                    )
                    occupied[mid] = vehicle.crossing_remaining_s
                    active.append(vehicle)
                    events.append({"time": round(t, 2), "kind": "entered",
                                   "vehicle_id": vehicle.vehicle_id,
                                   "movement_id": mid, "speed_kmh": round(vehicle.speed_mps * 3.6, 1)})
                else:
                    vehicle.distance_to_stop_m = max(0.0, vehicle.distance_to_stop_m - movement_distance)
                    if vehicle.speed_mps < 0.1:
                        vehicle.total_delay_s += time_step_s
                        stopped_vehicle_seconds += time_step_s
                    else:
                        speed_samples.append(vehicle.speed_mps * 3.6)
            # Keep a minimum physical gap in a queue.
            for index in range(1, len(queue)):
                leader = queue[index - 1]
                follower = queue[index]
                minimum_distance = leader.distance_to_stop_m + leader.profile.length_m + follower.profile.min_gap_m
                if follower.distance_to_stop_m < minimum_distance:
                    follower.distance_to_stop_m = minimum_distance
        max_queue = max(max_queue, sum(len(queue) for queue in queues.values()))
        elapsed += time_step_s

    return MicroSimulationResult(
        duration_seconds=int(elapsed), arrivals=arrivals, departed=departed,
        final_queue=sum(len(q) for q in queues.values()), max_queue=max_queue,
        average_speed_kmh=(sum(speed_samples) / len(speed_samples)) if speed_samples else 0.0,
        average_delay_s=(sum(delay_samples) / len(delay_samples)) if delay_samples else 0.0,
        stopped_vehicle_seconds=stopped_vehicle_seconds, events=events,
    )
