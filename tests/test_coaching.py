from app.ai.coaching import CoachOutput, build_coach_output
from app.ai.readiness import compute_readiness


def test_output_has_message_and_workout():
    snap = {"sleep_score": 80, "hrv_status": "balanced", "body_battery_high": 90,
            "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 53}
    out = build_coach_output(snap, compute_readiness(snap))
    assert isinstance(out, CoachOutput)
    assert out.message and isinstance(out.message, str)
    assert out.workout.type and out.workout.hr_zone


def test_high_readiness_suggests_intense_workout():
    snap = {"sleep_score": 90, "hrv_status": "balanced", "body_battery_high": 95,
            "load_ratio": 1.0, "resting_hr_7d_avg": 48, "resting_hr_30d_avg": 52}
    out = build_coach_output(snap, compute_readiness(snap))
    assert "Riposo" not in out.workout.type


def test_no_data_message_prompts_sync():
    out = build_coach_output({}, compute_readiness({}))
    assert "Sincronizza" in out.message


def test_provider_seam_delegates():
    from app.ai.provider import get_provider
    snap = {"sleep_score": 70, "hrv_status": "balanced", "body_battery_high": 80,
            "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 50}
    out = get_provider().coach(snap, compute_readiness(snap))
    assert isinstance(out, CoachOutput)
