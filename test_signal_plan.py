import json
import pytest
from app.signals.plan import Phase, SignalPlan
from app.integrations.road_network import validate_bbox, RoadNetworkError, load_geojson, save_geojson
from app.simulation.engine import SimulationConfig, run_simulation

def test_cycle_is_sum_of_all_phase_intervals():
    plan = SignalPlan([Phase("A", 30, 3, 2), Phase("B", 25, 3, 2)])
    assert plan.cycle_seconds == 65

def test_invalid_phase_is_rejected():
    with pytest.raises(ValueError):
        SignalPlan([Phase("A", 1, 3, 2), Phase("B", 25, 3, 2)])

def test_signal_plan_roundtrip(tmp_path):
    plan = SignalPlan.default(); path = tmp_path / "plan.json"
    plan.save(path)
    assert SignalPlan.load(path).cycle_seconds == plan.cycle_seconds

def test_bbox_rejects_too_large_area():
    with pytest.raises(RoadNetworkError):
        validate_bbox(-10, -10, 10, 10)

def test_geojson_roundtrip(tmp_path):
    data = {"type":"FeatureCollection", "features":[{"type":"Feature", "properties":{"highway":"residential"}, "geometry":{"type":"LineString", "coordinates":[[1,2],[3,4]]}}]}
    path = tmp_path / "roads.geojson"; save_geojson(data, path)
    assert len(load_geojson(path)["features"]) == 1

def test_simulation_is_reproducible_and_conserves_counts():
    cfg = SimulationConfig(900, 700, 10, 600, 1800, 42)
    plan = SignalPlan.default()
    a, b = run_simulation(cfg, plan), run_simulation(cfg, plan)
    assert a == b
    assert a.ns_served <= a.ns_arrivals and a.ew_served <= a.ew_arrivals
    assert a.ns_final_queue >= 0 and a.ew_final_queue >= 0


def test_phase_includes_flashing_green_and_red_in_cycle():
    plan = SignalPlan([Phase("A", 20, 3, 2, 4, 5)])
    assert plan.cycle_seconds == 34


def test_signal_plan_accepts_more_than_two_phases():
    plan = SignalPlan([
        Phase("A", 20, 3, 2, 3, 0),
        Phase("B", 25, 3, 2, 2, 4),
        Phase("C", 15, 3, 2, 1, 6),
    ])
    assert len(plan.phases) == 3
    assert plan.cycle_seconds == 91


def test_detailed_simulation_counts_vehicle_and_pedestrian_demand():
    from app.simulation.detailed import MovementDemand, PedestrianDemand, simulate_detailed
    plan = SignalPlan([
        Phase("Север-Юг", 30, 3, 2, movements=["Север:*", "Юг:*"]),
        Phase("Восток-Запад и пешеходы", 30, 3, 2,
              movements=["Восток:*", "Запад:*", "Пешеходы:Север"]),
    ])
    movements = [MovementDemand("Север", 1, "Прямо", 600), MovementDemand("Восток", 1, "Прямо", 450)]
    pedestrians = [PedestrianDemand("Север", 300)]
    result = simulate_detailed(movements, pedestrians, plan.phases, 600, 1800, 7)
    assert result.arrivals > 0
    assert result.pedestrian_arrivals > 0
    assert 0 <= result.served <= result.arrivals
    assert 0 <= result.pedestrians_served <= result.pedestrian_arrivals


def test_detailed_simulation_rejects_negative_demand():
    from app.simulation.detailed import MovementDemand, simulate_detailed
    with pytest.raises(ValueError):
        simulate_detailed([MovementDemand("Север", 1, "Прямо", -1)], [], SignalPlan.default().phases)


def test_default_plan_assigns_phases_to_separate_approaches():
    plan = SignalPlan.default()
    assert plan.phases[0].movements == ["Север:*", "Юг:*"]
    assert plan.phases[1].movements == ["Восток:*", "Запад:*"]


def test_detailed_simulation_does_not_guess_empty_phase_assignments():
    from app.simulation.detailed import MovementDemand, simulate_detailed
    plan = SignalPlan([Phase("Без назначения", 5, 0, 0)])
    result = simulate_detailed([MovementDemand("Север", 1, "Прямо", 3600)], [], plan.phases, 10, 1800, 1)
    assert result.arrivals == 10
    assert result.served == 0



def test_conflicting_opposing_left_turn_keeps_only_nonconflicting_movement():
    from app.simulation.detailed import MovementDemand, simulate_detailed
    phase = Phase("Конфликтная фаза", 20, 3, 2,
                  movements=["Север:1:Налево", "Юг:1:Прямо"])
    movements = [
        MovementDemand("Север", 1, "Налево", 3600),
        MovementDemand("Юг", 1, "Прямо", 3600),
    ]
    result = simulate_detailed(movements, [], [phase], 20, 3600, 1)
    assert result.arrivals == 40
    # The straight movement may proceed; the conflicting left turn remains queued.
    assert result.served == 20
    assert result.final_queue == 20
    assert result.phase_conflicts
    assert "КОНФЛИКТЫ ДВИЖЕНИЙ" in result.report()
    assert "стоп-линии" in result.report()


def test_perpendicular_traffic_movements_are_flagged_as_conflicting():
    from app.simulation.detailed import MovementDemand, find_phase_conflicts
    north = MovementDemand("Север", 1, "Прямо", 600)
    east = MovementDemand("Восток", 1, "Прямо", 600)
    conflicts = find_phase_conflicts(
        {north.movement_id, east.movement_id}, [north, east], []
    )
    assert conflicts == [(north.movement_id, east.movement_id)]


def test_vehicle_conflicts_with_pedestrian_crossing_on_its_approach():
    from app.simulation.detailed import MovementDemand, PedestrianDemand, find_phase_conflicts
    vehicle = MovementDemand("Север", 1, "Прямо", 600)
    pedestrian = PedestrianDemand("Север", 300)
    conflicts = find_phase_conflicts(
        {vehicle.movement_id, pedestrian.movement_id}, [vehicle], [pedestrian]
    )
    assert conflicts == [(vehicle.movement_id, pedestrian.movement_id)]



def test_conflict_resolution_keeps_safe_straight_movements_green():
    from app.simulation.detailed import MovementDemand, resolve_phase_movements
    north_straight = MovementDemand("Север", 1, "Прямо", 600)
    south_straight = MovementDemand("Юг", 1, "Прямо", 600)
    north_left = MovementDemand("Север", 1, "Налево", 200)
    movements = [north_straight, south_straight, north_left]
    permitted = resolve_phase_movements(
        {item.movement_id for item in movements}, movements, []
    )
    assert north_straight.movement_id in permitted
    assert south_straight.movement_id in permitted
    assert north_left.movement_id not in permitted



def test_right_turn_does_not_conflict_with_perpendicular_straight_vehicle():
    from app.simulation.detailed import MovementDemand, vehicle_movements_conflict
    right_turn = MovementDemand("Север", 1, "Направо", 600)
    cross_straight = MovementDemand("Восток", 1, "Прямо", 600)
    assert not vehicle_movements_conflict(right_turn, cross_straight)


def test_right_turn_conflicts_with_perpendicular_left_turn():
    from app.simulation.detailed import MovementDemand, vehicle_movements_conflict
    right_turn = MovementDemand("Север", 1, "Направо", 600)
    cross_left = MovementDemand("Восток", 1, "Налево", 600)
    assert vehicle_movements_conflict(right_turn, cross_left)


def test_mandatory_sign_restricts_vehicle_movement():
    from app.signs import movement_allowed_by_signs
    signs = [{"approach": "Север", "code": "4.1.2"}]
    assert movement_allowed_by_signs("Север", "Направо", signs)
    assert not movement_allowed_by_signs("Север", "Прямо", signs)
    assert not movement_allowed_by_signs("Север", "Налево", signs)


def test_prohibitory_sign_blocks_right_turn():
    from app.signs import movement_allowed_by_signs
    signs = [{"approach": "Запад", "code": "3.18.1"}]
    assert not movement_allowed_by_signs("Запад", "Направо", signs)
    assert movement_allowed_by_signs("Запад", "Прямо", signs)
