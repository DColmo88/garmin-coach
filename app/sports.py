"""Gli sport: come si chiamano, con che icona, e in che unità si misurano.

Garmin restituisce codici come `indoor_cycling` o `open_water_swimming`, e
finora finivano a schermo così com'erano. Peggio: la velocità veniva mostrata
sempre in minuti al chilometro, anche per la bici, dove nessuno ragiona in quel
modo — un giro a 23 km/h diventava «2:35/km», aritmeticamente giusto e
praticamente illeggibile.

Qui c'è un posto solo in cui si decide, per ogni tipo di attività:

- **come si chiama** in italiano;
- **che icona** gli tocca;
- **a che famiglia** appartiene (corsa, bici, nuoto, cammino, forza, altro);
- **se si misura a ritmo o a velocità.**

La corrispondenza è per sottostringa, non per elenco esatto: Garmin inventa
tipi nuovi di continuo (`gravel_cycling`, `virtual_ride`, `ebike_fitness`) e
un elenco chiuso invecchierebbe in un mese.
"""
from __future__ import annotations

from dataclasses import dataclass

# Le famiglie, che sono anche i due sport "principali" fra cui si sceglie.
RUNNING = "running"
CYCLING = "cycling"
SWIMMING = "swimming"
WALKING = "walking"
STRENGTH = "strength"
OTHER = "other"

PRIMARY_SPORTS = (RUNNING, CYCLING)


@dataclass(frozen=True)
class Sport:
    family: str
    label: str
    icon: str
    # Vero quando la prestazione si legge in km/h invece che in minuti al km.
    # In bici, in acqua e sugli sci nessuno ragiona in minuti al chilometro.
    uses_speed: bool = False


SPORTS: dict[str, Sport] = {
    RUNNING: Sport(RUNNING, "Corsa", "sport-run"),
    CYCLING: Sport(CYCLING, "Bici", "sport-bike", uses_speed=True),
    SWIMMING: Sport(SWIMMING, "Nuoto", "sport-swim", uses_speed=True),
    WALKING: Sport(WALKING, "Camminata", "sport-hike"),
    STRENGTH: Sport(STRENGTH, "Forza", "sport-gym"),
    OTHER: Sport(OTHER, "Altro", "sport-other", uses_speed=True),
}

# Ordine importante: la prima sottostringa che compare vince. `trail_running`
# deve incontrare "run" prima di qualunque altra cosa, e `mountain_biking`
# deve incontrare "bik" senza essere scambiato per una camminata in montagna.
_MATCHERS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("trail_run",), RUNNING, "Trail"),
    (("treadmill", "indoor_run"), RUNNING, "Tapis roulant"),
    (("virtual_run",), RUNNING, "Corsa virtuale"),
    (("run",), RUNNING, "Corsa"),
    (("mountain_bik", "mtb"), CYCLING, "MTB"),
    (("gravel",), CYCLING, "Gravel"),
    (("indoor_cycl", "virtual_ride", "spinning"), CYCLING, "Bici indoor"),
    (("ebike", "e_bike"), CYCLING, "E-bike"),
    (("cycl", "bik", "ride"), CYCLING, "Bici"),
    (("open_water",), SWIMMING, "Nuoto in acque libere"),
    (("swim",), SWIMMING, "Nuoto"),
    (("hik",), WALKING, "Escursione"),
    (("walk",), WALKING, "Camminata"),
    (("strength", "weight_train"), STRENGTH, "Forza"),
    (("yoga", "pilates", "stretch"), STRENGTH, "Mobilità"),
    (("cardio", "elliptical", "hiit"), STRENGTH, "Cardio"),
    (("ski", "snowboard"), OTHER, "Sci"),
    (("row",), OTHER, "Canottaggio"),
)


def classify(activity_type: str | None) -> tuple[Sport, str]:
    """(sport, nome specifico). Il nome è più preciso della famiglia.

    Una MTB e una bici da strada sono entrambe `CYCLING` — stessa icona, stessa
    unità — ma in tabella si vuole leggere quale delle due.
    """
    kind = (activity_type or "").lower()
    for needles, family, label in _MATCHERS:
        if any(needle in kind for needle in needles):
            return SPORTS[family], label
    return SPORTS[OTHER], _humanise(activity_type)


def _humanise(activity_type: str | None) -> str:
    """Uno sport sconosciuto resta leggibile: `resort_skiing` → «Resort skiing»."""
    if not activity_type:
        return "Attività"
    text = str(activity_type).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Attività"


def sport_of(activity) -> Sport:
    return classify(getattr(activity, "activity_type", None))[0]


def label_of(activity) -> str:
    return classify(getattr(activity, "activity_type", None))[1]


def icon_of(activity) -> str:
    return sport_of(activity).icon


def is_family(activity, family: str) -> bool:
    return sport_of(activity).family == family


# ============================================================================
# Velocità o ritmo
# ============================================================================


def speed_label(activity) -> str:
    """La prestazione nell'unità giusta per quello sport.

    Restituisce «5:06/km» per la corsa e «23,1 km/h» per la bici. Stringa vuota
    quando non c'è abbastanza per calcolarla: meglio una cella vuota che un
    numero che non vuol dire niente.
    """
    distance_m = getattr(activity, "distance_m", None)
    duration_sec = getattr(activity, "duration_sec", None)
    if not distance_m or not duration_sec or distance_m < 400:
        return ""

    if sport_of(activity).uses_speed:
        kmh = (distance_m / 1000) / (duration_sec / 3600)
        return f"{kmh:.1f} km/h".replace(".", ",")

    per_km = duration_sec / (distance_m / 1000)
    return f"{int(per_km // 60)}:{int(per_km % 60):02d}/km"


def speed_header(activity_types) -> str:
    """L'intestazione della colonna, quando la tabella mescola più sport."""
    families = {classify(t)[0].family for t in activity_types}
    if families == {CYCLING} or families == {SWIMMING}:
        return "Velocità"
    if CYCLING in families or SWIMMING in families or OTHER in families:
        return "Ritmo / vel."
    return "Ritmo"


# ============================================================================
# Lo sport principale dell'utente
# ============================================================================


def primary_sport(user) -> str:
    """Corsa o bici. È una preferenza di lettura, non un filtro sui dati.

    Cambia cosa si vede per primo e quali primati hanno senso; **non** esclude
    niente dai calcoli né dal contesto dell'AI, perché una persona può correre,
    andare in bici e sciare nella stessa settimana.
    """
    declared = (getattr(user, "primary_sport", None) or "").lower()
    return declared if declared in PRIMARY_SPORTS else RUNNING


def primary_label(user) -> str:
    return SPORTS[primary_sport(user)].label


def infer_primary_sport(activities) -> str | None:
    """Lo sport prevalente nello storico, per proporlo alla registrazione.

    Conta il **tempo**, non il numero di uscite: tre giri di rulli da venti
    minuti non fanno di uno un ciclista se poi corre due ore a settimana.
    """
    seconds = {RUNNING: 0.0, CYCLING: 0.0}
    for activity in activities:
        family = sport_of(activity).family
        if family in seconds:
            seconds[family] += float(getattr(activity, "duration_sec", None) or 0)

    if not any(seconds.values()):
        return None
    return max(seconds, key=lambda k: seconds[k])
