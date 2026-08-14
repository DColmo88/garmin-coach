"""Provider AI, quote e cache del coaching.

Nessun test contatta un modello reale: i client SDK sono sostituiti da finti.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.ai.base import AIProvider, Finished, TextChunk, ToolSpec, ToolStarted
from app.ai.readiness import compute_readiness
from app.db.models import AIUsageLog, DailyCoachCache, User

SNAP = {
    "sleep_score": 82, "hrv_status": "balanced", "body_battery_high": 90,
    "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 52,
    "resting_hr_latest": 50, "vo2max_latest": 52.4,
}


@pytest.fixture()
def user(db) -> User:
    u = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(u)
    db.commit()
    return u


# ============================================================================
# Factory
# ============================================================================

def test_factory_defaults_to_stub(monkeypatch):
    from app.ai.provider import StubProvider, get_provider

    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "stub")
    assert isinstance(get_provider(), StubProvider)


def test_factory_falls_back_to_stub_without_api_key(monkeypatch):
    """Una chiave mancante non deve far crashare le pagine."""
    from app.ai.provider import StubProvider, get_provider

    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "claude")
    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", "")
    assert isinstance(get_provider(), StubProvider)

    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "openai")
    monkeypatch.setattr("app.config.settings.OPENAI_API_KEY", "")
    assert isinstance(get_provider(), StubProvider)


def test_stub_does_not_support_chat():
    from app.ai.provider import StubProvider

    assert StubProvider().supports_chat is False


# ============================================================================
# Prompt: quanto materiale finisce nel contesto
# ============================================================================

def test_snapshot_lines_skip_missing_values():
    from app.ai.prompts import snapshot_lines

    text = snapshot_lines({"sleep_score": 80, "hrv_status": None, "vo2max_latest": None})
    assert "Sonno (score ieri): 80" in text
    assert "HRV" not in text and "VO2max" not in text


def test_chat_system_prompt_stays_compact():
    """Il system prompt deve restare piccolo: si paga a ogni messaggio."""
    from app.ai.prompts import chat_system_prompt

    prompt = chat_system_prompt("Davide", SNAP, compute_readiness(SNAP), None, "14/08/2026")
    # ~4 caratteri per token: sotto i 1000 token
    assert len(prompt) < 4000, f"system prompt troppo lungo: {len(prompt)} caratteri"
    assert "Davide" in prompt
    assert "Nessun obiettivo impostato" in prompt


def test_chat_system_prompt_includes_goal(db, user):
    from app import goals
    from app.ai.prompts import chat_system_prompt

    goal = goals.set_active_goal(
        db, user, "race_10k", target_date=date.today() + timedelta(days=30),
        params={"target_time": "50:00"},
    )
    prompt = chat_system_prompt("Davide", SNAP, compute_readiness(SNAP), goal, "14/08/2026")
    assert "Gara 10K" in prompt and "50:00" in prompt


def test_plan_prompt_summarises_instead_of_dumping():
    """Il prompt del piano non deve contenere la serie storica completa."""
    from app.ai.prompts import plan_user_prompt

    context = {
        "activities": [
            {"start_time": f"2026-07-{d:02d}T07:00:00", "type": "running",
             "distance_m": 10000, "duration_sec": 3000, "avg_hr": 150}
            for d in range(1, 29)
        ],
        "training": [{"vo2max": 52.0, "training_status": "productive"}],
        "wellness": [{"day": "2026-07-01", "steps": 9000}] * 28,
        "sleep": [{"day": "2026-07-01", "sleep_score": 80}] * 28,
    }
    prompt = plan_user_prompt(context, "Mezza maratona")
    assert "Mezza maratona" in prompt
    assert "52.0" in prompt
    # solo le 10 attività più recenti sono elencate per esteso
    assert prompt.count("· running ·") <= 10
    # nessuna riga di wellness o sonno
    assert "sleep_score" not in prompt and "steps" not in prompt


# ============================================================================
# Quote e consumo
# ============================================================================

def test_record_and_count(db, user):
    from app.ai import usage

    usage.record(db, user.id, "chat", model="claude-haiku-4-5", tokens_in=100, tokens_out=50)
    usage.record(db, user.id, "chat", model="claude-haiku-4-5", tokens_in=80, tokens_out=40)
    assert usage.count_today(db, user.id, "chat") == 2
    assert usage.count_today(db, user.id, "coach") == 0


def test_chat_quota_blocks_when_exhausted(db, user):
    from app.ai import usage

    user.ai_quota_chat_daily = 2
    db.commit()

    usage.check_quota(db, user, "chat")  # 0 usate: passa
    usage.record(db, user.id, "chat")
    usage.record(db, user.id, "chat")

    with pytest.raises(usage.QuotaExceeded) as exc:
        usage.check_quota(db, user, "chat")
    assert exc.value.used == 2 and exc.value.limit == 2
    assert usage.remaining(db, user, "chat") == 0


def test_plan_quota_is_monthly(db, user):
    from app.ai import usage

    user.ai_quota_plans_monthly = 1
    db.commit()
    usage.record(db, user.id, "plan")

    with pytest.raises(usage.QuotaExceeded):
        usage.check_quota(db, user, "plan")

    # una richiesta del mese scorso non conta
    old = db.query(AIUsageLog).one()
    old.day = date.today().replace(day=1) - timedelta(days=1)
    db.commit()
    usage.check_quota(db, user, "plan")


def test_coaching_has_no_quota(db, user):
    """Il coaching è già limitato dalla cache giornaliera."""
    from app.ai import usage

    for _ in range(50):
        usage.record(db, user.id, "coach")
    usage.check_quota(db, user, "coach")  # non solleva


def test_quota_is_per_user(db, user):
    from app.ai import usage

    other = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(other)
    db.commit()

    user.ai_quota_chat_daily = 1
    db.commit()
    usage.record(db, user.id, "chat")

    with pytest.raises(usage.QuotaExceeded):
        usage.check_quota(db, user, "chat")
    usage.check_quota(db, other, "chat")  # l'altro utente è intatto


def test_cost_estimate(db, user):
    from app.ai import usage

    # 1M token in + 1M out su Haiku = 1.00 + 5.00 dollari
    cost = usage.estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000)
    assert round(cost, 2) == 6.00

    usage.record(db, user.id, "chat", model="claude-haiku-4-5",
                 tokens_in=500_000, tokens_out=100_000)
    summary = usage.summary(db, user.id)
    assert summary.calls == 1
    assert summary.tokens_in == 500_000
    assert round(summary.cost_usd, 3) == round(0.5 * 1.00 + 0.1 * 5.00, 3)


def test_unknown_model_uses_default_price():
    from app.ai import usage

    assert usage.estimate_cost("modello-mai-visto", 1_000_000, 0) == 1.00


# ============================================================================
# Cache giornaliera: l'AI parla una volta al giorno
# ============================================================================

class CountingProvider(AIProvider):
    """Provider finto che conta quante volte viene interpellato."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "finto"

    def generate_training_plan(self, context, goal):
        return "piano finto"

    def coach(self, snap, readiness, goal=None):
        from app.ai.coaching import build_coach_output

        self.calls += 1
        out = build_coach_output(snap, readiness, goal)
        out.message = f"Messaggio AI numero {self.calls}"
        out.source = "ai"
        out.tokens_in, out.tokens_out = 400, 90
        out.model = "finto-light"
        return out


@pytest.fixture()
def user_with_data(db, user) -> User:
    from app.db.models import DailyWellness, SleepRecord, TrainingMetric

    today = date.today()
    db.add(DailyWellness(user_id=user.id, day=today, resting_hr=50, body_battery_high=90))
    db.add(SleepRecord(user_id=user.id, day=today, sleep_score=82, total_sleep_sec=27000))
    db.add(TrainingMetric(user_id=user.id, day=today, hrv_status="balanced", vo2max=52))
    db.commit()
    return user


def test_ai_message_is_generated_once_per_day(db, user_with_data, monkeypatch):
    from app import pipeline

    provider = CountingProvider()
    monkeypatch.setattr(pipeline, "get_provider", lambda: provider)

    first = pipeline.refresh_daily_cache(db, user_with_data)
    assert first.source == "ai"
    assert first.coach_message == "Messaggio AI numero 1"

    # cinque refresh successivi: nessuna nuova chiamata
    for _ in range(5):
        again = pipeline.refresh_daily_cache(db, user_with_data)
    assert provider.calls == 1
    assert again.coach_message == "Messaggio AI numero 1"


def test_ai_coaching_is_logged_for_billing(db, user_with_data, monkeypatch):
    from app import pipeline

    monkeypatch.setattr(pipeline, "get_provider", lambda: CountingProvider())
    pipeline.refresh_daily_cache(db, user_with_data)

    logs = db.query(AIUsageLog).all()
    assert len(logs) == 1
    assert logs[0].kind == "coach" and logs[0].tokens_in == 400


def test_force_ai_regenerates(db, user_with_data, monkeypatch):
    from app import pipeline

    provider = CountingProvider()
    monkeypatch.setattr(pipeline, "get_provider", lambda: provider)

    pipeline.refresh_daily_cache(db, user_with_data)
    row = pipeline.refresh_daily_cache(db, user_with_data, force_ai=True)
    assert provider.calls == 2
    assert row.coach_message == "Messaggio AI numero 2"


def test_readiness_still_recalculated_when_ai_cached(db, user_with_data, monkeypatch):
    """Il testo si riusa, ma i numeri si aggiornano a ogni sync."""
    from app import pipeline
    from app.db.models import SleepRecord

    monkeypatch.setattr(pipeline, "get_provider", lambda: CountingProvider())
    first = pipeline.refresh_daily_cache(db, user_with_data)
    first_score = first.readiness_score

    sleep = db.query(SleepRecord).one()
    sleep.sleep_score = 30
    db.commit()

    second = pipeline.refresh_daily_cache(db, user_with_data)
    assert second.readiness_score < first_score


def test_deterministic_provider_does_not_log_usage(db, user_with_data, monkeypatch):
    """Lo stub non consuma nulla: niente da registrare."""
    from app import pipeline
    from app.ai.provider import StubProvider

    monkeypatch.setattr(pipeline, "get_provider", lambda: StubProvider())
    row = pipeline.refresh_daily_cache(db, user_with_data)
    assert row.source == "deterministic"
    assert db.query(AIUsageLog).count() == 0


def test_coach_page_does_not_call_ai_on_every_load(logged_client, test_db, monkeypatch):
    """Aprire /coach dieci volte deve costare al massimo una chiamata."""
    import app.pipeline as pipeline

    provider = CountingProvider()
    monkeypatch.setattr(pipeline, "get_provider", lambda: provider)

    for _ in range(5):
        assert logged_client.get("/coach").status_code == 200
    assert provider.calls <= 1


# ============================================================================
# Provider Claude: loop dei tool con SDK finto
# ============================================================================

class FakeStream:
    def __init__(self, message, text_chunks):
        self._message = message
        self._chunks = text_chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        for chunk in self._chunks:
            yield SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text_delta", text=chunk),
            )

    def get_final_message(self):
        return self._message


def _msg(blocks, tokens_in=100, tokens_out=20):
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, arguments, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=arguments, id=block_id)


@pytest.fixture()
def claude(monkeypatch):
    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("app.config.settings.CLAUDE_MODEL_LIGHT", "finto-light")
    monkeypatch.setattr("app.config.settings.CLAUDE_MODEL_HEAVY", "finto-heavy")
    from app.ai.provider import ClaudeProvider

    provider = ClaudeProvider()
    return provider


TOOLS = [ToolSpec("get_activities", "Elenco attività", {"type": "object", "properties": {}})]


def test_claude_chat_without_tools(claude):
    claude.client = SimpleNamespace(
        messages=SimpleNamespace(
            stream=lambda **kw: FakeStream(_msg([_text_block("Ciao!")]), ["Ciao", "!"])
        )
    )
    events = list(claude.chat("sys", [{"role": "user", "content": "ciao"}], TOOLS, lambda n, a: ""))

    assert [e.text for e in events if isinstance(e, TextChunk)] == ["Ciao", "!"]
    done = events[-1]
    assert isinstance(done, Finished)
    assert done.tokens_in == 100 and done.model == "finto-light"
    assert done.tools_used == []


def test_claude_chat_executes_tools_then_answers(claude):
    responses = [
        _msg([_tool_block("get_activities", {"days": 7})]),
        _msg([_text_block("Hai corso 3 volte.")], tokens_in=200, tokens_out=30),
    ]
    chunks = [[], ["Hai corso 3 volte."]]
    calls = {"n": 0}

    def fake_stream(**kwargs):
        i = calls["n"]
        calls["n"] += 1
        return FakeStream(responses[i], chunks[i])

    claude.client = SimpleNamespace(messages=SimpleNamespace(stream=fake_stream))

    executed = []

    def execute(name, arguments):
        executed.append((name, arguments))
        return "3 corse"

    events = list(claude.chat("sys", [{"role": "user", "content": "?"}], TOOLS, execute))

    assert executed == [("get_activities", {"days": 7})]
    started = [e for e in events if isinstance(e, ToolStarted)]
    assert started[0].tool == "get_activities" and started[0].arguments == {"days": 7}
    done = events[-1]
    assert done.tokens_in == 300  # i token dei due giri si sommano
    assert done.tools_used == ["get_activities"]


def test_claude_chat_survives_failing_tool(claude):
    responses = [
        _msg([_tool_block("get_activities", {})]),
        _msg([_text_block("Non riesco a leggere quel dato.")]),
    ]
    calls = {"n": 0}

    def fake_stream(**kwargs):
        i = calls["n"]
        calls["n"] += 1
        return FakeStream(responses[i], [])

    claude.client = SimpleNamespace(messages=SimpleNamespace(stream=fake_stream))

    def exploding_tool(name, arguments):
        raise RuntimeError("database irraggiungibile")

    events = list(claude.chat("sys", [{"role": "user", "content": "?"}], TOOLS, exploding_tool))
    assert isinstance(events[-1], Finished)  # la conversazione non si interrompe


def test_claude_chat_stops_after_max_rounds(claude):
    """Un modello che chiama tool all'infinito non deve svuotare il portafoglio."""
    claude.client = SimpleNamespace(
        messages=SimpleNamespace(
            stream=lambda **kw: FakeStream(_msg([_tool_block("get_activities", {})]), [])
        )
    )
    events = list(
        claude.chat("sys", [{"role": "user", "content": "?"}], TOOLS,
                    lambda n, a: "dati", max_rounds=3)
    )
    assert len([e for e in events if isinstance(e, ToolStarted)]) == 3
    assert isinstance(events[-1], Finished)


def test_claude_coach_falls_back_when_api_fails(claude):
    """Se l'API non risponde si mostra comunque il messaggio deterministico."""
    def boom(**kwargs):
        raise RuntimeError("503 overloaded")

    claude.client = SimpleNamespace(messages=SimpleNamespace(create=boom))
    out = claude.coach(SNAP, compute_readiness(SNAP), None)
    assert out.source == "deterministic"
    assert "Prontezza" in out.message


def test_claude_coach_keeps_deterministic_workout(claude):
    """L'AI riscrive le parole, non cambia l'allenamento calcolato."""
    claude.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: _msg([_text_block("Oggi sei in forma, spingi.")], 300, 40)
        )
    )
    readiness = compute_readiness(SNAP)
    from app.ai.coaching import build_coach_output

    expected = build_coach_output(SNAP, readiness, None).workout

    out = claude.coach(SNAP, readiness, None)
    assert out.message == "Oggi sei in forma, spingi."
    assert out.source == "ai" and out.tokens_in == 300
    assert out.workout.type == expected.type


def test_claude_coach_skips_ai_without_data(claude):
    """Senza dati non ha senso pagare una chiamata."""
    claude.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kw: pytest.fail("l'AI non doveva essere chiamata")
        )
    )
    empty = {"sleep_score": None, "hrv_status": None, "body_battery_high": None,
             "load_ratio": None, "resting_hr_7d_avg": None, "resting_hr_30d_avg": None}
    out = claude.coach(empty, compute_readiness(empty), None)
    assert out.source == "deterministic"
