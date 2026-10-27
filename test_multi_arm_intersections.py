from app.editor.intersection_view import IntersectionView
from app.simulation.detailed import (
    MovementDemand,
    movement_exit_bearing,
    vehicle_movements_conflict,
)


def test_templates_expose_expected_active_arm_counts():
    assert len(IntersectionView.TEMPLATE_APPROACHES["Т-образный"]) == 3
    assert len(IntersectionView.TEMPLATE_APPROACHES["Классический 4-конечный"]) == 4
    assert len(IntersectionView.TEMPLATE_APPROACHES["5-конечный"]) == 5
    assert len(IntersectionView.TEMPLATE_APPROACHES["6-конечный"]) == 6


def test_diagonal_approach_has_exit_bearing():
    movement = MovementDemand("Северо-восток", 1, "Прямо", 300)
    assert movement_exit_bearing(movement) == 225


def test_diagonal_and_acute_angle_movements_are_conservatively_conflicting():
    first = MovementDemand("Север", 1, "Прямо", 300)
    second = MovementDemand("Юго-восток", 1, "Налево", 300)
    assert vehicle_movements_conflict(first, second)


def test_same_arm_movements_are_not_cross_arm_conflicts():
    first = MovementDemand("Северо-запад", 1, "Прямо", 100)
    second = MovementDemand("Северо-запад", 2, "Направо", 100)
    assert not vehicle_movements_conflict(first, second)
