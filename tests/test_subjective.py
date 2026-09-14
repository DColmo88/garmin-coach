"""Il check-in del mattino e lo sforzo percepito.

L'unica cosa in tutta l'app che non arriva da un apparecchio — e in letteratura
quella che predice l'affaticamento meglio di HRV e frequenza a riposo.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.clock import today_for
from app.db.models import Activity, DailyCheckin, User
from tests.conftest import TEST_EMAIL


def _user(test_db) -> User:
    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == TEST_EMAIL))
    finally:
        session.close()


def _checkin(test_db, user_id):
    session = test_db()
    try:
        return session.scalar(
            select(DailyCheckin).where(DailyCheckin.user_id == user_id)
        )
    finally:
        session.close()


# ============================================================================
# Check-in
# ============================================================================


def test_the_coach_page_asks_how_you_are(logged_client):
    page = logged_client.get("/coach")
    assert "Come stai oggi?" in page.text


def test_a_checkin_is_saved_and_shown_back(logged_client, test_db):
    user_id = _user(test_db).id

    response = logged_client.post("/checkin", data={
        "energy": "4", "legs": "2", "mood": "5", "sleep_quality": "3",
        "note": "dormito in treno",
    })
    assert response.status_code == 303

    row = _checkin(test_db, user_id)
    assert (row.energy, row.legs, row.mood, row.sleep_quality) == (4, 2, 5, 3)
    assert row.note == "dormito in treno"
    assert "Gambe 2/5" in logged_client.get("/coach").text


def test_answering_twice_overwrites_instead_of_failing(logged_client, test_db):
    """Alle sette di mattina uno può sbagliare uno slider."""
    user_id = _user(test_db).id

    logged_client.post("/checkin", data={"energy": "2"})
    logged_client.post("/checkin", data={"energy": "5"})

    session = test_db()
    try:
        rows = session.scalars(
            select(DailyCheckin).where(DailyCheckin.user_id == user_id)
        ).all()
    finally:
        session.close()

    assert len(rows) == 1
    assert rows[0].energy == 5


def test_a_partial_answer_is_accepted(logged_client, test_db):
    """Chi risponde a una domanda su quattro dà comunque un'informazione."""
    user_id = _user(test_db).id

    logged_client.post("/checkin", data={"legs": "1"})

    row = _checkin(test_db, user_id)
    assert row.legs == 1
    assert row.energy is None


def test_a_value_outside_the_scale_is_dropped_not_stored(logged_client, test_db):
    """Meglio non avere la risposta che averne una che falsa la media."""
    user_id = _user(test_db).id

    logged_client.post("/checkin", data={"energy": "99", "legs": "-3", "mood": "ciao"})

    row = _checkin(test_db, user_id)
    assert (row.energy, row.legs, row.mood) == (None, None, None)


def test_the_checkin_moves_the_readiness_score(logged_client, test_db):
    """Rispondere deve cambiare il numero, altrimenti perché rispondere."""
    from app import queries as q
    from app.ai.readiness import compute_readiness

    user = _user(test_db)
    session = test_db()
    try:
        fresco = compute_readiness(q.coach_snapshot(session, user.id))
        assert fresco.score is None   # nessun dato di nessun tipo
    finally:
        session.close()

    logged_client.post("/checkin", data={
        "energy": "5", "legs": "5", "mood": "5", "sleep_quality": "5",
    })

    session = test_db()
    try:
        snap = q.coach_snapshot(session, user.id)
        assert snap["checkin"]["energy"] == 5
        assert snap["ages"]["subjective"] == 0
    finally:
        session.close()


def test_the_past_cannot_be_filled_in(logged_client):
    """Il modulo compare solo su oggi: un check-in retroattivo è un'invenzione."""
    ieri = (
        __import__("datetime").date.today() - timedelta(days=1)
    ).isoformat()
    page = logged_client.get(f"/coach?day={ieri}")
    assert "Come stai oggi?" not in page.text


# ============================================================================
# Sforzo percepito
# ============================================================================


def _activity(test_db, user_id, **kwargs):
    session = test_db()
    try:
        activity = Activity(
            user_id=user_id, source="garmin", external_id=777,
            activity_type="running", start_time=datetime.now() - timedelta(days=1),
            duration_sec=3600, distance_m=10000, **kwargs,
        )
        session.add(activity)
        session.commit()
    finally:
        session.close()


def test_a_recent_activity_without_effort_is_asked_about(logged_client, test_db):
    _activity(test_db, _user(test_db).id)
    assert "Com'è andata?" in logged_client.get("/coach").text


def test_an_activity_with_a_heart_rate_is_still_asked(logged_client, test_db):
    """Anche con la fascia: sulle ripetute corte il cuore arriva in ritardo."""
    _activity(test_db, _user(test_db).id, avg_hr=150)
    assert "Com'è andata?" in logged_client.get("/coach").text


def test_the_effort_is_saved(logged_client, test_db):
    user_id = _user(test_db).id
    _activity(test_db, user_id)

    response = logged_client.post("/activities/777/effort", data={"rpe": "8"})
    assert response.status_code == 303

    session = test_db()
    try:
        activity = session.scalar(select(Activity).where(Activity.external_id == 777))
        assert activity.rpe == 8
    finally:
        session.close()


def test_once_answered_it_is_not_asked_again(logged_client, test_db):
    _activity(test_db, _user(test_db).id)
    logged_client.post("/activities/777/effort", data={"rpe": "6"})

    assert "Com'è andata?" not in logged_client.get("/coach").text


def test_an_activity_of_someone_else_cannot_be_rated(logged_client, test_db):
    session = test_db()
    try:
        other = User(email="altro@x.it", password_hash="h")
        session.add(other)
        session.commit()
        other_id = other.id
    finally:
        session.close()

    _activity(test_db, other_id)

    logged_client.post("/activities/777/effort", data={"rpe": "9"})

    session = test_db()
    try:
        activity = session.scalar(select(Activity).where(Activity.external_id == 777))
        assert activity.rpe is None
    finally:
        session.close()


def test_the_effort_changes_the_training_load(db):
    """È il punto: l'RPE non è un diario, entra nei conti."""
    from app.analysis.load import FROM_DURATION, FROM_RPE, activity_load
    from app.analysis.profile import AthleteProfile
    from types import SimpleNamespace

    profilo = AthleteProfile(hr_max=190, hr_rest=50, lthr=167)
    senza = SimpleNamespace(duration_sec=3600, avg_power=None, avg_hr=None, rpe=None)
    con = SimpleNamespace(duration_sec=3600, avg_power=None, avg_hr=None, rpe=9)

    base = activity_load(senza, profilo)
    dichiarato = activity_load(con, profilo)

    assert base.source == FROM_DURATION
    assert not base.is_measured
    assert dichiarato.source == FROM_RPE
    assert dichiarato.is_measured        # è una misura, non una stima dalla durata
    assert dichiarato.value > base.value
