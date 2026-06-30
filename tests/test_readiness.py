from app.ai.readiness import compute_readiness


def _base() -> dict:
    return {
        "sleep_score": 80, "hrv_status": "balanced", "body_battery_high": 90,
        "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 53,
    }


def test_high_readiness_band():
    r = compute_readiness(_base())
    assert r.score >= 80
    assert r.emoji == "🔥"
    assert r.label == "Pronto"
    assert len(r.breakdown) == 5


def test_all_missing_returns_none_score():
    r = compute_readiness({})
    assert r.score is None
    assert r.label == "Dati assenti"


def test_low_readiness_band():
    snap = {"sleep_score": 30, "hrv_status": "unbalanced", "body_battery_high": 20,
            "load_ratio": 1.8, "resting_hr_7d_avg": 60, "resting_hr_30d_avg": 55}
    r = compute_readiness(snap)
    assert r.score < 50
    assert r.emoji in {"🔵", "❌"}


def test_hrv_missing_uses_neutral_default():
    snap = dict(_base())
    snap["hrv_status"] = None
    hrv_factor = next(f for f in compute_readiness(snap).breakdown if f.name == "HRV")
    assert hrv_factor.value == 65
