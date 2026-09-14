"""Le schede di allenamento dentro le risposte del coach.

Quando il modello propone una seduta concreta emette, oltre alla spiegazione a
parole, un blocco delimitato:

    ```allenamento
    {"tipo": "ripetute", "titolo": "6 × 1000 m", ...}
    ```

Qui il testo viene diviso in segmenti — prosa e schede — così l'interfaccia può
rendere le schede come tali invece di mostrare del JSON in mezzo a una frase.

**Il parsing è volutamente indulgente.** Un modello che sbaglia una virgola non
deve far sparire la risposta: se il blocco non si legge, resta il testo grezzo,
che è comunque leggibile. Non è mai un errore fatale.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Il tag del blocco. `allenamento` e non `json`: dev'essere riconoscibile senza
# ambiguità, e il modello a volte emette anche JSON per altri motivi.
BLOCK_PATTERN = re.compile(
    r"```allenamento\s*\n(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)

# I tipi che l'app sa disegnare, con l'icona e la fascia di colore. L'ordine
# non conta, ma i nomi sì: sono gli stessi elencati nel prompt.
WORKOUT_TYPES: dict[str, dict[str, str]] = {
    "facile": {"icon": "facile", "label": "Fondo facile", "tone": "easy"},
    "lungo": {"icon": "lungo", "label": "Lungo", "tone": "long"},
    "ripetute": {"icon": "ripetute", "label": "Ripetute", "tone": "hard"},
    "soglia": {"icon": "soglia", "label": "Soglia", "tone": "hard"},
    "fartlek": {"icon": "fartlek", "label": "Fartlek", "tone": "hard"},
    "progressivo": {"icon": "progressivo", "label": "Progressivo", "tone": "medium"},
    "salite": {"icon": "salite", "label": "Salite", "tone": "hard"},
    "riposo": {"icon": "riposo", "label": "Riposo", "tone": "rest"},
    "forza": {"icon": "forza", "label": "Forza", "tone": "medium"},
    "bici": {"icon": "bici", "label": "Bici", "tone": "easy"},
    "nuoto": {"icon": "nuoto", "label": "Nuoto", "tone": "easy"},
}
_FALLBACK = {"icon": "facile", "label": "Allenamento", "tone": "medium"}


@dataclass
class Block:
    """Un segmento della parte centrale: una serie, un tratto, un blocco.

    `durata` non era nel contratto iniziale: l'ha aggiunta il modello alla prima
    prova vera («Corsa continua, 6:00-6:30/km, 30'»), ed era ragionevole. Meglio
    accettarla che perderla.
    """

    cosa: str
    ritmo: str = ""
    durata: str = ""
    recupero: str = ""


@dataclass
class Workout:
    """Una seduta proposta dal coach, pronta da disegnare."""

    tipo: str
    titolo: str
    obiettivo: str = ""
    durata_min: int | None = None
    riscaldamento: str = ""
    blocchi: list[Block] = field(default_factory=list)
    defaticamento: str = ""
    zona_fc: str = ""
    razionale: str = ""
    note: str = ""
    giorno: str = ""

    @property
    def icon(self) -> str:
        return WORKOUT_TYPES.get(self.tipo, _FALLBACK)["icon"]

    @property
    def type_label(self) -> str:
        return WORKOUT_TYPES.get(self.tipo, _FALLBACK)["label"]

    @property
    def tone(self) -> str:
        return WORKOUT_TYPES.get(self.tipo, _FALLBACK)["tone"]

    @property
    def duration_label(self) -> str:
        if not self.durata_min:
            return ""
        hours, minutes = divmod(int(self.durata_min), 60)
        return f"{hours}h {minutes:02d}m" if hours else f"{minutes} min"

    def as_dict(self) -> dict[str, Any]:
        """Per il rendering lato browser, che riceve gli stessi campi."""
        return {
            "tipo": self.tipo,
            "icon": self.icon,
            "type_label": self.type_label,
            "tone": self.tone,
            "titolo": self.titolo,
            "obiettivo": self.obiettivo,
            "duration_label": self.duration_label,
            "riscaldamento": self.riscaldamento,
            "blocchi": [
                {"cosa": b.cosa, "ritmo": b.ritmo, "durata": b.durata,
                 "recupero": b.recupero}
                for b in self.blocchi
            ],
            "defaticamento": self.defaticamento,
            "zona_fc": self.zona_fc,
            "razionale": self.razionale,
            "note": self.note,
            "giorno": self.giorno,
        }


@dataclass
class Segment:
    """Un pezzo di risposta: o prosa, o una scheda."""

    kind: str  # "text" | "workout"
    text: str = ""
    workout: Workout | None = None


def _as_str(value: Any) -> str:
    """Il modello a volte manda una lista dove ci si aspetta una frase."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_as_str(v) for v in value if v).strip()
    return str(value).strip()


def _as_minutes(value: Any) -> int | None:
    """«65», «65 min», «1h 05» → minuti. Quello che non si capisce si scarta."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if 0 < value <= 600 else None
    numbers = re.findall(r"\d+", str(value))
    if not numbers:
        return None
    minutes = int(numbers[0])
    return minutes if 0 < minutes <= 600 else None


def parse_workout(payload: Any) -> Workout | None:
    """Da dizionario a `Workout`. `None` se manca l'essenziale."""
    if not isinstance(payload, dict):
        return None

    titolo = _as_str(payload.get("titolo"))
    tipo = _as_str(payload.get("tipo")).lower()
    if not titolo and not tipo:
        return None

    blocchi = []
    raw_blocks = payload.get("blocchi")
    if isinstance(raw_blocks, list):
        for item in raw_blocks:
            if isinstance(item, dict):
                cosa = _as_str(item.get("cosa"))
                if not cosa:
                    continue
                blocchi.append(Block(
                    cosa=cosa,
                    ritmo=_as_str(item.get("ritmo")),
                    durata=_as_str(item.get("durata")),
                    recupero=_as_str(item.get("recupero")),
                ))
            elif isinstance(item, str) and item.strip():
                # Il modello a volte semplifica in una lista di stringhe.
                blocchi.append(Block(cosa=item.strip()))

    return Workout(
        tipo=tipo if tipo in WORKOUT_TYPES else "",
        titolo=titolo or WORKOUT_TYPES.get(tipo, _FALLBACK)["label"],
        obiettivo=_as_str(payload.get("obiettivo")),
        durata_min=_as_minutes(payload.get("durata_min")),
        riscaldamento=_as_str(payload.get("riscaldamento")),
        blocchi=blocchi,
        defaticamento=_as_str(payload.get("defaticamento")),
        zona_fc=_as_str(payload.get("zona_fc")),
        razionale=_as_str(payload.get("razionale")),
        note=_as_str(payload.get("note")),
        giorno=_as_str(payload.get("giorno")),
    )


def split(text: str) -> list[Segment]:
    """Divide una risposta in prosa e schede, nell'ordine in cui compaiono.

    Se un blocco non è JSON valido resta nel testo così com'è: meglio del JSON
    a vista che una risposta amputata.
    """
    if not text or "```allenamento" not in text.lower():
        return [Segment("text", text)] if text else []

    segments: list[Segment] = []
    cursor = 0

    for match in BLOCK_PATTERN.finditer(text):
        try:
            workout = parse_workout(json.loads(match.group(1)))
        except (json.JSONDecodeError, TypeError, ValueError):
            workout = None
        if workout is None:
            continue  # blocco illeggibile: resta dentro il testo

        before = text[cursor:match.start()].strip()
        if before:
            segments.append(Segment("text", before))
        segments.append(Segment("workout", workout=workout))
        cursor = match.end()

    tail = text[cursor:].strip()
    if tail:
        segments.append(Segment("text", tail))

    return segments or [Segment("text", text)]


def has_workout(text: str) -> bool:
    return any(s.kind == "workout" for s in split(text))
