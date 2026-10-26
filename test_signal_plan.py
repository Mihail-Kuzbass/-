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
