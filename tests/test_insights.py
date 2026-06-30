from app.ai.insights import detect_insights, top_insights


def test_resting_hr_drop_fires_green():
    snap = {"resting_hr_7d_avg": 50, "resting_hr_30d_avg": 55}
    titles = [i.title for i in detect_insights(snap)]
    assert any("FC riposo" in t for t in titles)


def test_load_spike_fires_red():
    snap = {"load_ratio": 1.5}
    found = [i for i in detect_insights(snap) if i.color == "red"]
    assert found and "overtraining" in found[0].text.lower()


def test_days_since_run_fires():
    snap = {"days_since_last_run": 6}
    assert any("senza corsa" in i.text for i in detect_insights(snap))


def test_top_insights_orders_red_first_and_caps():
    snap = {
        "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 55,   # green
        "load_ratio": 1.6,                                    # red
        "sleep_score_7d_avg": 60,                             # amber
        "consecutive_active_days": 8,                         # green
    }
    top = top_insights(snap, n=3)
    assert len(top) == 3
    assert top[0].color == "red"


def test_no_data_yields_empty():
    assert detect_insights({}) == []
