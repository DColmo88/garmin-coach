"""Piani di allenamento: generazione, lettura, adattamento alla prontezza."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import goals, training
from app.ai.base import AIProvider, AIProviderError
from app.db.models import AIUsageLog, TrainingPlan, User


def make_plan_json(weeks: int = 4) -> dict:
    return {
        "title": "10K in 4 settimane",
        "rationale": "Costruiamo sulla base che hai già.",
        "weeks": [
            {
                "number": n,
                "focus": "volume" if n < weeks else "scarico",
                "total_km": 30 + n,
                "days": [
                    {"weekday": "lunedì", "type": "riposo", "detail": "Riposo", "note": ""},
                    {"weekday": "martedì", "type": "facile", "detail": "8 km facili", "note": ""},
                    {"weekday": "mercoledì", "type": "intervalli",
                     "detail": "6×800m", "note": "Recupero 2 minuti"},
                    {"weekday": "giovedì", "type": "riposo", "detail": "Riposo", "note": ""},
                    {"weekday": "venerdì", "type": "facile", "detail": "6 km facili", "note": ""},
                    {"weekday": "sabato", "type": "ritmo", "detail": "5 km a ritmo gara",
                     "note": ""},
                    {"weekday": "domenica", "type": "lungo", "detail": "14 km lenti", "note": ""},
                ],
            }
            for n in range(1, weeks + 1)
        ],
    }


class PlanProvider(AIProvider):
    """Provider finto che restituisce un piano strutturato."""

    heavy = "finto-heavy"

    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload if payload is not None else make_plan_json()
        self.error = error
        self.received_schema = None
        self.received_goal = None

    @property
    def name(self) -> str:
        return "finto"

    def generate_training_plan(self, context, goal, schema=None):
        if self.error:
            raise self.error
        self.received_schema = schema
        self.received_goal = goal
        return self.payload


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def with_goal(db, user) -> User:
    goals.set_active_goal(db, user, "race_10k", target_date=date.today() + timedelta(days=28))
    return user


def use_provider(monkeypatch, provider):
    monkeypatch.setattr("app.training.get_provider", lambda: provider)


# ============================================================================
# Generazione
# ============================================================================

def test_plan_requires_a_goal(db, user, monkeypatch):
    use_provider(monkeypatch, PlanProvider())
    with pytest.raises(training.PlanError) as exc:
        training.generate_plan(db, user)
    assert "obiettivo" in str(exc.value).lower()


def test_generate_saves_the_plan(db, with_goal, monkeypatch):
    provider = PlanProvider()
    use_provider(monkeypatch, provider)

    plan = training.generate_plan(db, with_goal)
    assert plan.active is True
    assert plan.weeks_total == 4
    assert plan.plan_json["title"] == "10K in 4 settimane"
    assert plan.start_date is not None


def test_generate_passes_the_schema_and_the_goal(db, with_goal, monkeypatch):
    provider = PlanProvider()
    use_provider(monkeypatch, provider)
    training.generate_plan(db, with_goal)

    assert provider.received_schema is training.PLAN_SCHEMA
    assert "Gara 10K" in provider.received_goal
    assert "settimane" in provider.received_goal


def test_plan_length_follows_the_race_date(db, user, monkeypatch):
    provider = PlanProvider()
    use_provider(monkeypatch, provider)
    goals.set_active_goal(db, user, "race_half", target_date=date.today() + timedelta(days=70))

    training.generate_plan(db, user)
    assert "10 settimane" in provider.received_goal


def test_generating_a_new_plan_archives_the_old_one(db, with_goal, monkeypatch):
    use_provider(monkeypatch, PlanProvider())
    first = training.generate_plan(db, with_goal)
    second = training.generate_plan(db, with_goal)

    db.refresh(first)
    assert first.active is False and second.active is True
    assert training.get_active_plan(db, with_goal.id).id == second.id


def test_generation_is_logged_against_the_quota(db, with_goal, monkeypatch):
    use_provider(monkeypatch, PlanProvider())
    training.generate_plan(db, with_goal)

    log = db.query(AIUsageLog).one()
    assert log.kind == "plan" and log.model == "finto-heavy"


def test_quota_blocks_generation(db, with_goal, monkeypatch):
    from app.ai import usage

    use_provider(monkeypatch, PlanProvider())
    with_goal.ai_quota_plans_monthly = 1
    db.commit()

    training.generate_plan(db, with_goal)
    with pytest.raises(usage.QuotaExceeded):
        training.generate_plan(db, with_goal)


def test_provider_failure_becomes_a_readable_error(db, with_goal, monkeypatch):
    use_provider(monkeypatch, PlanProvider(error=AIProviderError("529 overloaded")))
    with pytest.raises(training.PlanError) as exc:
        training.generate_plan(db, with_goal)
    assert "non ha risposto" in str(exc.value)


def test_empty_plan_is_rejected(db, with_goal, monkeypatch):
    use_provider(monkeypatch, PlanProvider(payload={"title": "x", "weeks": []}))
    with pytest.raises(training.PlanError):
        training.generate_plan(db, with_goal)


def test_json_wrapped_in_markdown_is_accepted(db, with_goal, monkeypatch):
    """Alcuni modelli incorniciano il JSON in un blocco di codice."""
    import json

    payload = "```json\n" + json.dumps(make_plan_json(2)) + "\n```"
    use_provider(monkeypatch, PlanProvider(payload=payload))

    plan = training.generate_plan(db, with_goal)
    assert plan.weeks_total == 2


def test_unreadable_response_is_rejected(db, with_goal, monkeypatch):
    use_provider(monkeypatch, PlanProvider(payload="non sono JSON"))
    with pytest.raises(training.PlanError):
        training.generate_plan(db, with_goal)


def test_stub_provider_refuses_structured_plans():
    from app.ai.provider import StubProvider

    with pytest.raises(AIProviderError):
        StubProvider().generate_training_plan({}, "10K", schema=training.PLAN_SCHEMA)

    # senza schema continua a restituire il markdown esplicativo
    assert "non ancora configurato" in StubProvider().generate_training_plan({}, "10K")


# ============================================================================
# Lettura del piano
# ============================================================================

@pytest.fixture()
def plan(db, with_goal, monkeypatch) -> TrainingPlan:
    use_provider(monkeypatch, PlanProvider())
    return training.generate_plan(db, with_goal)


def test_current_week_before_the_start_is_one(db, plan):
    plan.start_date = date.today() + timedelta(days=3)
    assert training.current_week_number(plan) == 1


def test_current_week_advances(db, plan):
    plan.start_date = date.today() - timedelta(days=10)
    assert training.current_week_number(plan) == 2


def test_current_week_is_capped_at_the_plan_length(db, plan):
    plan.start_date = date.today() - timedelta(days=365)
    assert training.current_week_number(plan) == plan.weeks_total


def test_progress_percentage(db, plan):
    plan.start_date = date.today() - timedelta(days=7)
    assert training.progress_pct(plan) == 50  # settimana 2 di 4


def test_week_data_by_number(db, plan):
    assert training.week_data(plan, 2)["number"] == 2
    assert training.week_data(plan, 99) is None


# ============================================================================
# Adattamento alla prontezza — il pezzo deterministico
# ============================================================================

def wednesday_of_week_one(plan) -> date:
    """Un mercoledì (giorno di intervalli) della prima settimana."""
    plan.start_date = date.today() - timedelta(days=date.today().weekday())
    monday = plan.start_date
    return monday + timedelta(days=2)


def test_hard_session_is_kept_when_ready(db, plan):
    day = wednesday_of_week_one(plan)
    session = training.today_session(plan, readiness_score=80, today=day)
    assert session.type == "intervalli"
    assert session.adapted is False


def test_hard_session_is_softened_when_tired(db, plan):
    day = wednesday_of_week_one(plan)
    session = training.today_session(plan, readiness_score=45, today=day)
    assert session.type == "facile"
    assert session.adapted is True
    assert "45" in session.adaptation_reason


def test_hard_session_becomes_rest_when_exhausted(db, plan):
    day = wednesday_of_week_one(plan)
    session = training.today_session(plan, readiness_score=25, today=day)
    assert session.type == "riposo"
    assert session.adapted is True
    assert "domani" in session.adaptation_reason


def test_easy_session_is_never_adapted(db, plan):
    plan.start_date = date.today() - timedelta(days=date.today().weekday())
    tuesday = plan.start_date + timedelta(days=1)  # facile
    session = training.today_session(plan, readiness_score=20, today=tuesday)
    assert session.type == "facile" and session.adapted is False


def test_rest_day_stays_rest(db, plan):
    plan.start_date = date.today() - timedelta(days=date.today().weekday())
    monday = plan.start_date
    session = training.today_session(plan, readiness_score=20, today=monday)
    assert session.type == "riposo" and session.adapted is False


def test_missing_readiness_leaves_the_session_alone(db, plan):
    day = wednesday_of_week_one(plan)
    session = training.today_session(plan, readiness_score=None, today=day)
    assert session.type == "intervalli" and session.adapted is False


def test_session_carries_an_icon(db, plan):
    day = wednesday_of_week_one(plan)
    assert training.today_session(plan, 80, day).icon


# ============================================================================
# Isolamento e pagine
# ============================================================================

def test_plans_are_isolated_between_users(db, with_goal, monkeypatch):
    use_provider(monkeypatch, PlanProvider())
    training.generate_plan(db, with_goal)

    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()
    assert training.get_active_plan(db, other.id) is None


def test_plan_page_without_goal_asks_for_one(logged_client):
    page = logged_client.get("/plan").text
    assert "Prima serve un obiettivo" in page


def test_plan_page_offers_generation_once_there_is_a_goal(logged_client):
    logged_client.post("/goals", data={"goal_type": "race_10k"})
    page = logged_client.get("/plan").text
    assert "Genera il piano" in page


def test_plan_page_shows_the_calendar(logged_client, test_db, monkeypatch):
    monkeypatch.setattr("app.training.get_provider", lambda: PlanProvider())
    logged_client.post("/goals", data={"goal_type": "race_10k"})
    logged_client.post("/plan/generate")

    page = logged_client.get("/plan").text
    assert "10K in 4 settimane" in page
    assert "6×800m" in page
    assert "Settimana 1" in page


def test_plan_generation_error_is_shown(logged_client, monkeypatch):
    monkeypatch.setattr(
        "app.training.get_provider",
        lambda: PlanProvider(error=AIProviderError("boom")),
    )
    logged_client.post("/goals", data={"goal_type": "race_10k"})
    response = logged_client.post("/plan/generate")
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_plan_can_be_archived(logged_client, test_db, monkeypatch):
    monkeypatch.setattr("app.training.get_provider", lambda: PlanProvider())
    logged_client.post("/goals", data={"goal_type": "race_10k"})
    logged_client.post("/plan/generate")
    logged_client.post("/plan/archive")

    assert "Non hai un piano attivo" in logged_client.get("/plan").text


# ============================================================================
# Il piano arriva sulla home
# ============================================================================
# Chi segue un programma ha già una risposta per oggi. Mostrargli accanto il
# consiglio generico dell'AI significherebbe dargli due indicazioni diverse
# per la stessa giornata, e la seconda ignora il piano che sta seguendo.

def _activate_plan(test_db, user_id: int, days=None) -> None:
    """Mette un piano attivo che parte oggi, con i giorni che servono."""
    from datetime import date

    from app.db.models import TrainingPlan

    payload = make_plan_json()
    if days is not None:
        payload["weeks"][0]["days"] = days

    session = test_db()
    session.add(TrainingPlan(
        user_id=user_id, weeks_total=len(payload["weeks"]),
        plan_json=payload, start_date=date.today(), active=True,
    ))
    session.commit()
    session.close()


def _today_named(kind: str, detail: str, note: str = "") -> list[dict]:
    """Una settimana in cui *oggi* è la sessione voluta."""
    from datetime import date

    from app.training import WEEKDAYS

    today_name = WEEKDAYS[date.today().weekday()]
    return [
        {"weekday": d, "type": kind if d == today_name else "riposo",
         "detail": detail if d == today_name else "Riposo",
         "note": note if d == today_name else ""}
        for d in WEEKDAYS
    ]


def test_without_a_plan_the_home_keeps_the_generic_advice(logged_client):
    body = logged_client.get("/coach").text

    assert "Allenamento suggerito" in body
    assert "Oggi dal tuo piano" not in body


def test_with_a_plan_the_home_shows_todays_session(logged_client, logged_user, test_db):
    _activate_plan(test_db, logged_user.id, days=_today_named("facile", "8 km facili"))

    body = logged_client.get("/coach").text

    assert "Oggi dal tuo piano" in body
    assert "8 km facili" in body
    # E non il consiglio generico: sarebbero due risposte per la stessa domanda.
    assert "Allenamento suggerito" not in body


def test_the_session_note_is_carried_over(logged_client, logged_user, test_db):
    _activate_plan(test_db, logged_user.id,
                   days=_today_named("intervalli", "6×800m", note="Recupero 2 minuti"))

    body = logged_client.get("/coach").text

    assert "6×800m" in body
    assert "Recupero 2 minuti" in body


def test_the_home_says_which_week_it_is(logged_client, logged_user, test_db):
    _activate_plan(test_db, logged_user.id, days=_today_named("facile", "8 km facili"))

    assert "Settimana 1" in logged_client.get("/coach").text


def test_a_day_the_plan_leaves_empty_does_not_fall_back(logged_client, logged_user, test_db):
    """Se il piano non prevede niente, lo dice: non rimpiazza con altro."""
    _activate_plan(test_db, logged_user.id, days=[])

    body = logged_client.get("/coach").text

    assert "Il piano non prevede niente per oggi" in body
    assert "Allenamento suggerito" not in body


def test_the_home_links_to_the_week(logged_client, logged_user, test_db):
    _activate_plan(test_db, logged_user.id, days=_today_named("facile", "8 km facili"))

    assert "Vedi la settimana" in logged_client.get("/coach").text


def test_an_archived_plan_gives_the_generic_advice_back(logged_client, logged_user, test_db):
    from app import training

    _activate_plan(test_db, logged_user.id, days=_today_named("facile", "8 km facili"))
    session = test_db()
    training.archive_plan(session, logged_user.id)
    session.close()

    body = logged_client.get("/coach").text

    assert "Allenamento suggerito" in body
    assert "Oggi dal tuo piano" not in body


def test_a_softened_session_says_so_on_the_home(logged_client, logged_user, test_db, monkeypatch):
    """L'adattamento va detto, non applicato di nascosto.

    Chi segue un piano si accorge che oggi c'è scritto altro rispetto al
    programma, e la prima domanda è «perché».
    """
    from app.ai.readiness import ReadinessResult

    _activate_plan(test_db, logged_user.id,
                   days=_today_named("intervalli", "6×800m", note="Recupero 2 minuti"))

    # Prontezza bassa ma non bassissima: la sessione dura resta, ammorbidita.
    monkeypatch.setattr(
        "app.routers.pages.compute_readiness",
        lambda snap: ReadinessResult(42, "Basso", "🟠", "Vacci piano.", []),
    )

    body = logged_client.get("/coach").text

    assert "Oggi dal tuo piano" in body
    assert "facile al posto della sessione dura" in body
    assert "Prontezza a 42" in body
    assert "6×800m" not in body, "la sessione originale non è più il consiglio di oggi"


def test_a_very_low_readiness_turns_it_into_rest(logged_client, logged_user, test_db, monkeypatch):
    from app.ai.readiness import ReadinessResult

    _activate_plan(test_db, logged_user.id,
                   days=_today_named("intervalli", "6×800m", note="Recupero 2 minuti"))

    monkeypatch.setattr(
        "app.routers.pages.compute_readiness",
        lambda snap: ReadinessResult(28, "Molto basso", "🔴", "Riposa.", []),
    )

    body = logged_client.get("/coach").text

    assert "Riposo" in body
    assert "la sposti a domani" in body


def test_an_easy_session_is_never_touched_on_the_home(logged_client, logged_user, test_db, monkeypatch):
    """Una sessione facile con prontezza bassa resta facile: è già il rimedio."""
    from app.ai.readiness import ReadinessResult

    _activate_plan(test_db, logged_user.id, days=_today_named("facile", "6 km facili"))

    monkeypatch.setattr(
        "app.routers.pages.compute_readiness",
        lambda snap: ReadinessResult(28, "Molto basso", "🔴", "Riposa.", []),
    )

    body = logged_client.get("/coach").text

    assert "6 km facili" in body
    assert "la sposti a domani" not in body
