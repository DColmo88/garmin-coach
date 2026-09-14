"""Insight e letture delle pagine scritti dall'AI.

Il principio non cambia — *il determinismo fa i numeri, l'AI fa le parole* —
ma si applica a due punti in cui prima non era applicato: gli insight del
giorno e i verdetti in cima a Sonno, Recupero, Corpo e Forma.

Quello che questi test presidiano non è la qualità della prosa, che non si può
asserire: è che **il confine regga**. L'AI non deve poter cambiare un tono, non
deve poter riempire una sezione che non ha misurazioni, e quando non risponde
la pagina deve avere comunque qualcosa da dire.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.ai import readings
from app.ai.insights import generate_insights
from app.db.models import DailyWellness, SleepRecord, User


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.flush()
    for i in range(20):
        day = date.today() - timedelta(days=i)
        db.add(SleepRecord(user_id=u.id, day=day, sleep_score=70 - i,
                           total_sleep_sec=7 * 3600, deep_sleep_sec=5400))
        db.add(DailyWellness(user_id=u.id, day=day, resting_hr=55,
                             body_battery_high=70, avg_stress=30))
    db.commit()
    return u


class FakeProvider:
    """Provider finto: restituisce quello che gli si dice, e registra com'è stato chiamato."""

    def __init__(self, answer=None):
        self.answer = answer
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "finto"

    def analyse(self, system, user, schema, heavy=False):
        self.calls.append({"system": system, "user": user, "heavy": heavy})
        return self.answer


def use(monkeypatch, provider, where="app.ai.provider.get_provider"):
    monkeypatch.setattr(where, lambda: provider)


# ============================================================================
# Insight: la rete di sicurezza
# ============================================================================

def test_without_ai_the_fixed_insights_are_used(db, user, monkeypatch):
    """Senza chiave configurata la home non resta vuota."""
    def boom():
        raise RuntimeError("nessuna chiave")

    monkeypatch.setattr("app.ai.provider.get_provider", boom)

    insights, origin = generate_insights(db, user)
    assert origin == "deterministic"


def test_a_silent_model_falls_back(db, user, monkeypatch):
    use(monkeypatch, FakeProvider(answer=None))

    _, origin = generate_insights(db, user, briefing="BRIEFING")
    assert origin == "deterministic"


def test_a_malformed_answer_falls_back(db, user, monkeypatch):
    use(monkeypatch, FakeProvider(answer={"insights": "non una lista"}))

    _, origin = generate_insights(db, user, briefing="BRIEFING")
    assert origin == "deterministic"


def test_entries_without_a_title_are_dropped(db, user, monkeypatch):
    use(monkeypatch, FakeProvider(answer={"insights": [
        {"title": "", "text": "senza titolo", "color": "green"},
        {"title": "Buono", "text": "con testo", "color": "green"},
    ]}))

    insights, origin = generate_insights(db, user, briefing="BRIEFING")
    assert origin == "ai"
    assert [i.title for i in insights] == ["Buono"]


def test_an_invented_colour_becomes_amber(db, user, monkeypatch):
    use(monkeypatch, FakeProvider(answer={"insights": [
        {"title": "Titolo", "text": "Testo", "color": "fucsia"},
    ]}))

    insights, _ = generate_insights(db, user, briefing="BRIEFING")
    assert insights[0].color == "amber"


def test_no_more_than_three(db, user, monkeypatch):
    use(monkeypatch, FakeProvider(answer={"insights": [
        {"title": f"T{i}", "text": "x", "color": "green"} for i in range(6)
    ]}))

    insights, _ = generate_insights(db, user, briefing="BRIEFING")
    assert len(insights) == 3


def test_an_empty_list_is_a_valid_answer(db, user, monkeypatch):
    """Certi giorni non c'è niente di notevole, e il prompt lo dice.

    Ripiegare sulle frasi fisse per riempire lo spazio rimetterebbe in pagina
    esattamente le ovvietà da cui si sta scappando.
    """
    use(monkeypatch, FakeProvider(answer={"insights": []}))

    insights, origin = generate_insights(db, user, briefing="BRIEFING")
    assert insights == []
    assert origin == "ai"


def test_the_big_model_is_used(db, user, monkeypatch):
    """Il modello leggero ha letto al contrario un confronto settimanale.

    Un insight sbagliato è peggio di nessun insight, e qui si parla di una
    chiamata al giorno: non è il posto dove si risparmia.
    """
    provider = FakeProvider(answer={"insights": []})
    use(monkeypatch, provider)

    generate_insights(db, user, briefing="BRIEFING")
    assert provider.calls[0]["heavy"] is True


# ============================================================================
# Insight: le regole anti-banalità sono davvero nel prompt
# ============================================================================

def test_the_prompt_forbids_the_filler_phrases():
    from app.ai.prompts import INSIGHTS_SYSTEM

    for vietata in ("è importante", "è fondamentale", "monitorare", "ottimizzare"):
        assert vietata in INSIGHTS_SYSTEM, f"«{vietata}» non è fra le frasi vietate"


def test_the_prompt_demands_a_relation_and_a_number():
    from app.ai.prompts import INSIGHTS_SYSTEM

    assert "almeno due cose" in INSIGHTS_SYSTEM
    assert "cita un numero" in INSIGHTS_SYSTEM
    # Zero insight deve essere una risposta legittima, o si riempie per forza.
    assert "Zero è una risposta legittima" in INSIGHTS_SYSTEM


def test_the_prompt_says_not_to_repeat_what_is_on_screen():
    from app.ai.prompts import INSIGHTS_SYSTEM

    assert "già a schermo" in INSIGHTS_SYSTEM


# ============================================================================
# Letture delle pagine: il confine che non si supera
# ============================================================================

def _reading(verdict="Verdetto calcolato", tone="warn", enough=True):
    from app.insights.domains import PageReading

    return PageReading(verdict=verdict, evidence="le evidenze", action="azione calcolata",
                       tone=tone, enough_data=enough)


def test_the_ai_words_replace_the_verdict_and_the_action():
    out = readings.apply(_reading(), {"verdict": "Nuovo titolo", "action": "Nuova azione"})

    assert out.verdict == "Nuovo titolo"
    assert out.action == "Nuova azione"


def test_the_ai_cannot_change_the_tone():
    """Se le soglie dicono rosso, nessun modello può decidere che va bene."""
    out = readings.apply(_reading(tone="bad"), {"verdict": "Tutto sotto controllo",
                                                "action": "Rilassati"})

    assert out.tone == "bad"


def test_the_evidence_stays_the_one_that_was_computed():
    out = readings.apply(_reading(), {"verdict": "X", "action": "Y"})

    assert out.evidence == "le evidenze"


def test_without_an_answer_the_computed_reading_survives():
    base = _reading()
    assert readings.apply(base, None).verdict == base.verdict
    assert readings.apply(base, {}).verdict == base.verdict
    assert readings.apply(base, {"verdict": ""}).verdict == base.verdict


def test_a_section_without_measurements_is_never_rewritten():
    """Senza dati non c'è niente da dire meglio.

    La frase scritta a mano almeno spiega come uscire dal non avere dati; una
    frase generica del modello no.
    """
    base = _reading(verdict="Servono più misurazioni", tone="neutral", enough=False)

    out = readings.apply(base, {"verdict": "Il peso varia", "action": "Monitoralo"})
    assert out.verdict == "Servono più misurazioni"


def test_sections_without_measurements_are_not_even_sent(db, user, monkeypatch):
    provider = FakeProvider(answer={})
    use(monkeypatch, provider)
    # L'utente del fixture non ha misurazioni di peso: «corpo» non deve partire.
    readings.generate(db, user, "BRIEFING")

    assert provider.calls, "nessuna chiamata: il test non sta provando niente"
    assert "SEZIONE CORPO" not in provider.calls[0]["user"]


def test_all_four_pages_travel_in_one_call(db, user, monkeypatch):
    """Una chiamata sola, non quattro: costa meno e il modello vede il quadro."""
    provider = FakeProvider(answer={})
    use(monkeypatch, provider)

    readings.generate(db, user, "BRIEFING")
    assert len(provider.calls) == 1


def test_an_empty_verdict_leaves_the_fallback(db, user, monkeypatch):
    use(monkeypatch, FakeProvider(answer={
        "sonno": {"verdetto": "", "azione": ""},
        "recupero": {"verdetto": "Detto bene", "azione": "Fai questo"},
    }))

    out = readings.generate(db, user, "BRIEFING") or {}
    assert "sleep" not in out
    assert out["health"]["verdict"] == "Detto bene"


def test_the_readings_prompt_demands_a_number_in_the_action():
    from app.ai.prompts import READINGS_SYSTEM

    assert "dentro ci deve stare un numero" in READINGS_SYSTEM
    assert "Non puoi cambiare il tono" in READINGS_SYSTEM
    for vietata in ("monitora", "valuta di", "igiene del sonno"):
        assert vietata in READINGS_SYSTEM
