"""Le schede di allenamento dentro le risposte del coach.

Il principio che questi test presidiano: **un blocco malformato non deve mai
far sparire una risposta**. Il modello sbaglia una virgola ogni tanto; quando
succede resta il testo, che è comunque leggibile.
"""
from __future__ import annotations

import json

from app.ai.workout_card import WORKOUT_TYPES, has_workout, parse_workout, split

GOOD = {
    "tipo": "ripetute",
    "titolo": "6 × 1000 m a ritmo soglia",
    "obiettivo": "Alzare la velocità sostenibile",
    "durata_min": 65,
    "riscaldamento": "15' lento + 4 allunghi",
    "blocchi": [
        {"cosa": "6 × 1000 m", "ritmo": "4:15-4:25/km", "recupero": "2' cammino"}
    ],
    "defaticamento": "10' molto lento",
    "zona_fc": "Z4 · 157-176 bpm",
    "razionale": "Tre settimane senza qualità.",
}


def _block(payload) -> str:
    return "```allenamento\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


# --------------------------- divisione del testo ---------------------------

def test_prose_without_blocks_stays_one_segment():
    segments = split("Oggi riposa, vieni da tre giorni pieni.")
    assert len(segments) == 1
    assert segments[0].kind == "text"


def test_a_block_becomes_a_card_between_two_pieces_of_prose():
    text = f"Ecco cosa farei.\n\n{_block(GOOD)}\n\nFammi sapere come va."
    segments = split(text)

    assert [s.kind for s in segments] == ["text", "workout", "text"]
    assert segments[0].text == "Ecco cosa farei."
    assert segments[2].text == "Fammi sapere come va."
    assert segments[1].workout.titolo == "6 × 1000 m a ritmo soglia"


def test_more_blocks_become_more_cards():
    """Una settimana intera: un blocco per seduta."""
    lunedi = {**GOOD, "giorno": "Lunedì"}
    mercoledi = {**GOOD, "tipo": "lungo", "titolo": "Lungo 16 km", "giorno": "Mercoledì"}
    text = f"La settimana:\n{_block(lunedi)}\n{_block(mercoledi)}"

    cards = [s.workout for s in split(text) if s.kind == "workout"]

    assert len(cards) == 2
    assert cards[0].giorno == "Lunedì"
    assert cards[1].type_label == "Lungo"


def test_a_broken_block_leaves_the_answer_readable():
    """Il caso che conta: il modello sbaglia il JSON, la risposta resta."""
    text = "Ti propongo questo.\n```allenamento\n{rotto, non è json}\n```\nDimmi tu."
    segments = split(text)

    assert all(s.kind == "text" for s in segments)
    assert "Ti propongo questo." in segments[0].text
    assert "Dimmi tu." in segments[0].text


def test_an_empty_answer_produces_nothing():
    assert split("") == []


def test_has_workout_is_a_shortcut():
    assert has_workout(_block(GOOD))
    assert not has_workout("Nessun allenamento qui.")


def test_the_tag_is_case_insensitive():
    text = "```ALLENAMENTO\n" + json.dumps(GOOD) + "\n```"
    assert has_workout(text)


# --------------------------- lettura del blocco ---------------------------

def test_every_declared_type_has_an_icon_and_a_tone():
    for key, meta in WORKOUT_TYPES.items():
        assert meta["icon"] and meta["label"] and meta["tone"]
        assert meta["tone"] in {"easy", "long", "medium", "hard", "rest"}


def test_an_unknown_type_falls_back_instead_of_crashing():
    workout = parse_workout({**GOOD, "tipo": "yoga_lunare"})
    assert workout is not None
    assert workout.icon and workout.type_label == "Allenamento"


def test_the_duration_is_read_from_text_too():
    """Il modello scrive «65 min» invece di 65 più spesso di quanto si creda."""
    assert parse_workout({**GOOD, "durata_min": "65 min"}).durata_min == 65
    assert parse_workout({**GOOD, "durata_min": 65}).durata_min == 65
    assert parse_workout({**GOOD, "durata_min": "un'oretta"}).durata_min is None


def test_an_absurd_duration_is_discarded():
    assert parse_workout({**GOOD, "durata_min": 5000}).durata_min is None


def test_the_duration_label_reads_like_a_person_would_say_it():
    assert parse_workout({**GOOD, "durata_min": 45}).duration_label == "45 min"
    assert parse_workout({**GOOD, "durata_min": 95}).duration_label == "1h 35m"
    assert parse_workout({**GOOD, "durata_min": None}).duration_label == ""


def test_blocks_given_as_plain_strings_still_work():
    """Il modello a volte semplifica: non è un motivo per perdere la scheda."""
    workout = parse_workout({**GOOD, "blocchi": ["10 km a ritmo medio"]})
    assert len(workout.blocchi) == 1
    assert workout.blocchi[0].cosa == "10 km a ritmo medio"


def test_a_field_given_as_a_list_is_flattened():
    workout = parse_workout({**GOOD, "note": ["Terreno piano.", "Occhio al vento."]})
    assert workout.note == "Terreno piano. Occhio al vento."


def test_a_payload_without_title_or_type_is_not_a_workout():
    assert parse_workout({"obiettivo": "boh"}) is None
    assert parse_workout("una stringa") is None
    assert parse_workout(None) is None


def test_missing_fields_are_simply_absent():
    workout = parse_workout({"tipo": "riposo", "titolo": "Riposo completo"})
    assert workout.riscaldamento == ""
    assert workout.blocchi == []
    assert workout.type_label == "Riposo"


def test_a_block_measured_in_time_keeps_its_duration():
    """Il modello l'ha usato alla prima prova vera: «Corsa continua, 30'»."""
    workout = parse_workout({
        **GOOD,
        "blocchi": [{"cosa": "Corsa continua", "ritmo": "6:00-6:30/km", "durata": "30'"}],
    })
    assert workout.blocchi[0].durata == "30'"
    assert workout.as_dict()["blocchi"][0]["durata"] == "30'"
