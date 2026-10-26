"""Deterministic-seed, time-step queue model for two conflicting approaches.
This is an aggregate educational model, not a calibrated microsimulation.
"""
from dataclasses import dataclass
import random

@dataclass
class SimulationConfig:
    ns_demand_vph: int
    ew_demand_vph: int
    heavy_percent: float
    duration_seconds: int
    saturation_vph: int
    seed: int

@dataclass
class SimulationResult:
    config: SimulationConfig
    cycle_seconds: int
    ns_arrivals: int
    ew_arrivals: int
    ns_served: int
    ew_served: int
    ns_max_queue: int
    ew_max_queue: int
    total_queue_vehicle_seconds: float
    ns_final_queue: int = 0
    ew_final_queue: int = 0

    @property
    def total_served(self):
        return self.ns_served + self.ew_served

    @property
    def average_delay_seconds(self):
        return self.total_queue_vehicle_seconds / self.total_served if self.total_served else 0.0

    def to_report(self):
        c = self.config
        return (
            "ОТЧЁТ — АГРЕГИРОВАННАЯ ПОШАГОВАЯ МОДЕЛЬ\n\n"
            f"Длительность: {c.duration_seconds} с\nЦикл: {self.cycle_seconds} с\nSeed: {c.seed}\n"
            f"Интенсивность С—Ю / В—З: {c.ns_demand_vph} / {c.ew_demand_vph} авт./ч\n"
            f"Доля тяжёлых ТС: {c.heavy_percent:.1f}% (учтена приближённым снижением насыщенного потока)\n\n"
            f"Прибыло: {self.ns_arrivals + self.ew_arrivals} авт.\nОбслужено: {self.total_served} авт.\n"
            f"Север—Юг: прибыло {self.ns_arrivals}, обслужено {self.ns_served}, "
            f"макс. очередь {self.ns_max_queue}, остаток {self.ns_final_queue}\n"
            f"Восток—Запад: прибыло {self.ew_arrivals}, обслужено {self.ew_served}, "
            f"макс. очередь {self.ew_max_queue}, остаток {self.ew_final_queue}\n"
            f"Средняя оценочная задержка обслуженного ТС: {self.average_delay_seconds:.1f} с\n"
            f"Суммарное накопление очереди: {self.total_queue_vehicle_seconds:.0f} авт·с\n\n"
            "ОГРАНИЧЕНИЯ: два агрегированных направления; прибытия пуассоновские, обслуживание усреднено по секундам. "
            "В агрегированной модели обслуживаются только фазы 1 и 2; дополнительные фазы учитываются в длине цикла, "
            "но не назначаются отдельным направлениям. Нет отдельных полос, поворотов, перестроений, пешеходов, координации и калибровки. "
            "Не использовать как самостоятельное инженерное обоснование проекта."
        )

def run_simulation(cfg, plan):
    if cfg.duration_seconds <= 0 or cfg.saturation_vph <= 0:
        raise ValueError("Длительность и насыщенный поток должны быть положительными.")
    if not (0 <= cfg.heavy_percent <= 100):
        raise ValueError("Доля тяжёлых ТС должна быть от 0 до 100%.")
    if cfg.ns_demand_vph < 0 or cfg.ew_demand_vph < 0:
        raise ValueError("Интенсивности не могут быть отрицательными.")
    plan.validate()
    rng = random.Random(cfg.seed)
    nsq = ewq = nsmax = ewmax = 0
    nsa = ewa = nss = ews = 0
    queue_seconds = 0.0
    cycle = plan.cycle_seconds
    # Aggregate model maps only phase 1 to N-S and phase 2 to E-W.
    # Flashing green remains permissive for traffic; red/yellow/intergreen are non-service.
    ns_green = plan.phases[0].green_seconds + plan.phases[0].flashing_green_seconds
    ew_green = (plan.phases[1].green_seconds + plan.phases[1].flashing_green_seconds) if len(plan.phases) > 1 else 0
    ew_start = plan.phases[0].duration_seconds
    effective_saturation = cfg.saturation_vph / (1.0 + cfg.heavy_percent / 100.0 * 0.5)
    service_per_second = effective_saturation / 3600.0
    ns_credit = ew_credit = 0.0
    for t in range(cfg.duration_seconds):
        if rng.random() < cfg.ns_demand_vph / 3600.0:
            nsq += 1; nsa += 1
        if rng.random() < cfg.ew_demand_vph / 3600.0:
            ewq += 1; ewa += 1
        pos = t % cycle
        ns_active = pos < ns_green
        ew_active = ew_start <= pos < ew_start + ew_green
        if ns_active:
            ns_credit += service_per_second
            while nsq and ns_credit >= 1:
                nsq -= 1; nss += 1; ns_credit -= 1
        if ew_active:
            ew_credit += service_per_second
            while ewq and ew_credit >= 1:
                ewq -= 1; ews += 1; ew_credit -= 1
        nsmax = max(nsmax, nsq); ewmax = max(ewmax, ewq)
        queue_seconds += nsq + ewq
    return SimulationResult(cfg, cycle, nsa, ewa, nss, ews, nsmax, ewmax,
                            queue_seconds, nsq, ewq)
