from app.ai.insights import detect_insights, top_insights


def test_resting_hr_drop_fires_green():
    snap = {"resting_hr_7d_avg": 50, "resting_hr_30d_avg": 55}
    titles = [i.title for i in detect_insights(snap)]
    assert any("FC riposo" in t for t in titles)


def test_load_spike_fires_red():
    snap = {"load_ratio": 1.5}
    found = [i for i in detect_insights(snap) if i.color == "red"]
    assert found
    assert "1,5 volte" in found[0].text
    assert "giorni facili" in found[0].text


def test_deep_negative_form_fires_red():
    """La forma sotto −30 non è una settimana pesante: è fatica oltre la fitness."""
    found = [i for i in detect_insights({"tsb": -40}) if i.color == "red"]
    assert found and "fatica" in found[0].title.lower()


def test_a_big_positive_form_is_flagged_as_an_opportunity():
    found = [i for i in detect_insights({"tsb": 30}) if i.color == "green"]
    assert found and "scarico" in found[0].title.lower()


def test_the_grey_zone_becomes_an_insight():
    found = [i for i in detect_insights({"grey_time_pct": 40}) if i.color == "amber"]
    assert found and "zona 3" in found[0].text


def test_a_monotonous_week_is_flagged():
    found = [i for i in detect_insights({"monotony": 2.4}) if i.color == "amber"]
    assert found and "uguale" in found[0].title.lower()


def test_a_normal_form_produces_no_load_insight():
    """Le soglie sono severe: un valore in mezzo non deve generare rumore."""
    insights = detect_insights({"tsb": 2, "load_ratio": 1.0, "monotony": 1.2})
    assert insights == []


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


def test_insights_carry_no_emoji():
    """Il colore basta: la scheda lo mostra come barra laterale.

    Un pittogramma davanti a ogni frase faceva sembrare la pagina una chat.
    """
    snap = {
        "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 55,
        "load_ratio": 1.6, "sleep_score_7d_avg": 60, "tsb": -40,
        "grey_time_pct": 40, "monotony": 2.4, "days_since_last_run": 8,
    }
    for insight in detect_insights(snap):
        assert insight.title.isascii() or "₂" in insight.title
        assert insight.title[0].isalpha()


def test_a_cache_row_written_before_the_change_does_not_break_the_page(
    logged_client, test_db
):
    """Le righe già in cache hanno una chiave 'icon' in più.

    Il ricalcolo le riscrive subito, ma la pagina non deve dipendere da quello:
    il giorno del deploy non ci deve essere una finestra in cui esplode.
    """
    from datetime import date

    from sqlalchemy import select

    from app.db.models import DailyCoachCache, User

    session = test_db()
    user = session.scalar(select(User).where(User.email == "test@x.it"))
    session.add(DailyCoachCache(
        user_id=user.id, day=date.today(), readiness_score=70,
        readiness_label="Buono",
        insights_json=[{"icon": "🔋", "title": "Vecchio", "text": "t", "color": "amber"}],
    ))
    session.commit()
    session.close()

    assert logged_client.get("/coach").status_code == 200


def test_the_old_cache_shape_still_builds_an_insight():
    """Il costruttore legge per chiave e ignora quelle che non conosce più."""
    from app.ai.insights import Insight

    row = {"icon": "🔋", "title": "Vecchio", "text": "t", "color": "amber"}
    insight = Insight(row.get("title", ""), row.get("text", ""), row.get("color", "green"), 0)

    assert insight.title == "Vecchio"
    assert not hasattr(insight, "icon")
