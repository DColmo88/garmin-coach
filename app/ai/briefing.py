"""Il briefing che l'AI riceve: non com'è oggi, ma com'è oggi **per te**.

Il problema che questo modulo risolve. La versione precedente mandava al modello
una manciata di valori puntuali:

    - Sonno (score ieri): 41
    - Body Battery max: 28

Con quello davanti, qualunque modello scrive «riposa». Ma se le sei notti
precedenti erano fra 78 e 84, quel 41 è **una notte storta, non una crisi**: la
risposta giusta è «hai dormito male stanotte, ma vieni da una settimana buona,
tieni il programma e semmai accorcia».

Il modello non può saperlo se non gli si dice. Quindi qui si costruiscono tre
cose che prima non esistevano:

1. **La serie**, non il punto. Quattordici giorni in una riga sola: il modello
   vede la forma dell'andamento, non l'ultimo campione.
2. **Il confronto col proprio normale.** Non «sonno 41», ma «sonno 41, il
   minimo degli ultimi 30 giorni, quando il tuo tipico è 72-85, e sei fuori
   norma da un giorno solo».
3. **Gli allenamenti, sempre.** Prima il modello doveva chiedere un tool per
   sapere cosa avevi fatto. Erano i dati più rilevanti di tutti e non c'erano.

Costa qualche centinaio di token in più a chiamata. È il posto giusto dove
spenderli: tutto il resto dell'architettura serve a non sprecarli altrove.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from sqlalchemy.orm import Session

from app import queries as q
from app.db.models import User
from app.clock import today_for

# Quanti giorni di serie mostrare. Due settimane: abbastanza da vedere una
# tendenza, poche abbastanza da stare in una riga.
SERIES_DAYS = 14
# Su quanti giorni si calcola «il tuo normale».
BASELINE_DAYS = 30
# Sotto questo numero di misurazioni non si parla di normale: si direbbe solo
# che due valori sono diversi fra loro.
MIN_POINTS_FOR_BASELINE = 8
# Quanti allenamenti mettere sempre nel contesto.
RECENT_ACTIVITIES = 8


@dataclass
class Baseline:
    """Un valore di oggi, letto contro l'abitudine della persona."""

    label: str
    today: float | None
    typical_low: float | None
    typical_high: float | None
    median: float | None
    higher_is_better: bool
    days_outside: int = 0  # da quanti giorni consecutivi è fuori norma
    is_extreme: str = ""  # "minimo" | "massimo" | ""
    # Da quanti giorni è il valore chiamato «oggi». Zero è oggi davvero.
    age_days: int = 0
    unit: str = ""
    # Le ore vogliono un decimale; punteggi e battiti no.
    decimals: int = 0

    @property
    def has_baseline(self) -> bool:
        return self.typical_low is not None and self.typical_high is not None

    @property
    def position(self) -> str:
        """Dove cade il valore di oggi rispetto al proprio normale."""
        if self.today is None:
            return "non misurato"
        if not self.has_baseline:
            return "storico insufficiente per dire cosa è normale"
        if self.today < self.typical_low:
            return "sotto la norma" if self.higher_is_better else "sotto la norma, in meglio"
        if self.today > self.typical_high:
            return "sopra la norma, in meglio" if self.higher_is_better else "sopra la norma"
        return "nella norma"

    def as_line(self) -> str:
        if self.today is None:
            return f"{self.label}: non misurato oggi"

        d = self.decimals
        # L'età va detta, non nascosta: «Sonno 7,4 h» riferito a tre notti fa
        # è il tipo di imprecisione su cui un modello costruisce un consiglio
        # sbagliato, e non ha modo di accorgersene.
        quando = "" if not self.age_days else (
            " (ieri)" if self.age_days == 1 else f" ({self.age_days} giorni fa)"
        )
        parts = [f"{self.label} {_n(self.today, d)}{self.unit}{quando}", self.position]

        if self.has_baseline:
            parts.append(
                f"tipico {_n(self.typical_low, d)}-{_n(self.typical_high, d)}, "
                f"mediana {_n(self.median, d)}"
            )
        if self.is_extreme:
            parts.append(f"è il {self.is_extreme} degli ultimi {BASELINE_DAYS} giorni")
        if self.days_outside >= 1:
            giorno = "giorno" if self.days_outside == 1 else "giorni"
            parts.append(f"fuori norma da {self.days_outside} {giorno}")

        return " — ".join(parts)


def _n(value: float | None, decimals: int = 0) -> str:
    """Un numero leggibile. `decimals` serve alle ore, dove la mezz'ora conta.

    Con l'arrotondamento all'intero «7,5 ore» diventava «8 h», che su una
    domanda come «ho dormito abbastanza?» e' mezz'ora di differenza inventata.
    """
    if value is None:
        return "—"
    if decimals:
        return f"{value:.{decimals}f}".replace(".", ",")
    return str(int(round(value)))


def _clean(values: list) -> list[float]:
    return [float(v) for v in values if v is not None]


def _quantile(sorted_values: list[float], fraction: float) -> float:
    """Quantile per interpolazione lineare. Su liste corte `statistics` è brusco."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def build_baseline(label: str, by_day: dict, today: date, higher_is_better: bool,
                   unit: str = "", decimals: int = 0) -> Baseline:
    """Il confronto col proprio normale, a partire da una serie **per data**.

    Il «tipico» è l'intervallo fra il 25° e il 75° percentile: metà delle
    giornate ci cadono dentro. È più onesto di media ± deviazione standard su
    dati che non sono affatto normali (il sonno ha code lunghe da un lato solo).

    Due cose che prima sbagliava, entrambe dovute al fatto che leggeva una
    lista invece di un calendario:

    - **«oggi» era l'ultimo valore esistente.** Con le misurazioni ripulite dai
      buchi, `values[-1]` poteva essere di tre notti prima e veniva presentato
      al modello come la notte appena passata. Adesso si cerca la data, e se il
      valore è più vecchio lo si dice.
    - **«fuori norma da N giorni» contava misurazioni, non giorni.** Con due
      notti non registrate in mezzo, tre misurazioni storte diventavano «fuori
      norma da 3 giorni» coprendone cinque. Adesso si cammina all'indietro sul
      calendario e un giorno senza dato interrompe la serie invece di sparire.
    """
    values = _clean(by_day.values())

    # Il valore «di oggi» è quello di oggi; se manca si accetta il più recente
    # entro tre giorni, dichiarandone l'età. Oltre, non si parla di oggi.
    value, age = None, 0
    for back in range(0, 4):
        found = by_day.get(today - timedelta(days=back))
        if found is not None:
            value, age = float(found), back
            break

    baseline = Baseline(
        label=label, today=value, typical_low=None, typical_high=None,
        median=None, higher_is_better=higher_is_better, unit=unit,
        decimals=decimals, age_days=age,
    )
    if len(values) < MIN_POINTS_FOR_BASELINE:
        return baseline

    ordered = sorted(values)
    baseline.typical_low = _quantile(ordered, 0.25)
    baseline.typical_high = _quantile(ordered, 0.75)
    baseline.median = median(ordered)

    if value is not None:
        if value <= ordered[0]:
            baseline.is_extreme = "minimo"
        elif value >= ordered[-1]:
            baseline.is_extreme = "massimo"

        # Da quanti **giorni di calendario** consecutivi si è fuori dalla fascia
        # tipica. È il numero che distingue «una notte storta» da «una settimana
        # storta», e contarlo sulle misurazioni invece che sui giorni lo
        # gonfiava ogni volta che una notte non veniva registrata.
        low, high = baseline.typical_low, baseline.typical_high
        cursor = today - timedelta(days=age)
        while True:
            current = by_day.get(cursor)
            if current is None or low <= float(current) <= high:
                break
            baseline.days_outside += 1
            cursor -= timedelta(days=1)

    return baseline


# ============================================================================
# Relazioni: i confronti che l'AI non deve dedurre da sola
# ============================================================================
#
# Un modello che riceve solo serie di numeri o dice l'ovvio («hai dormito
# poco») o si inventa un legame che nei dati non c'è. Le relazioni si calcolano
# qui, in Python, e gli arrivano già fatte: così l'insight interessante può
# scriverlo senza avere niente da indovinare.
#
# Sotto questo numero di giorni per gruppo un confronto non si fa: due notti
# contro tre non dicono niente, e presentarle come una tendenza è peggio che
# tacere.
MIN_PER_GROUP = 3


def _by_day(rows, attr: str) -> dict:
    out = {}
    for r in rows:
        value = getattr(r, attr, None)
        if value is not None:
            out[r.day] = float(value)
    return out


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _week_over_week(label: str, by_day: dict, today: date,
                    unit: str = "", decimals: int = 0) -> str | None:
    """Media degli ultimi 7 giorni contro i 7 precedenti."""
    recent = [by_day[d] for i in range(7) if (d := today - timedelta(days=i)) in by_day]
    before = [by_day[d] for i in range(7, 14) if (d := today - timedelta(days=i)) in by_day]
    if len(recent) < MIN_PER_GROUP or len(before) < MIN_PER_GROUP:
        return None

    now, prev = _mean(recent), _mean(before)
    delta = now - prev
    verso = "=" if abs(delta) < 0.05 else ("+" if delta > 0 else "")
    return (f"{label}: {_n(now, decimals)}{unit} questa settimana contro "
            f"{_n(prev, decimals)}{unit} la precedente ({verso}{_n(delta, decimals)})")


def _after_training(label: str, by_day: dict, loads: dict, today: date,
                    unit: str = "", decimals: int = 0) -> str | None:
    """Il valore del giorno **dopo** un allenamento, contro dopo un giorno scarico.

    È la relazione che nessuno guarda da solo e che spiega più di tutte: se il
    sonno crolla sistematicamente dopo le sedute dure, non è una notte storta,
    è il modo in cui questo corpo reagisce al carico.
    """
    dopo_carico, dopo_riposo = [], []
    for i in range(BASELINE_DAYS):
        day = today - timedelta(days=i)
        if day not in by_day:
            continue
        ieri = loads.get(day - timedelta(days=1), 0.0)
        (dopo_carico if ieri > 0 else dopo_riposo).append(by_day[day])

    if len(dopo_carico) < MIN_PER_GROUP or len(dopo_riposo) < MIN_PER_GROUP:
        return None

    con, senza = _mean(dopo_carico), _mean(dopo_riposo)
    return (f"{label} il giorno dopo un allenamento: {_n(con, decimals)}{unit} "
            f"({len(dopo_carico)} volte) contro {_n(senza, decimals)}{unit} "
            f"dopo un giorno scarico ({len(dopo_riposo)} volte)")


def relation_lines(hours: dict, quality: dict, battery: dict, rhr: dict,
                   loads: dict, today: date) -> list[str]:
    """I confronti che valgono la pena di essere passati al modello.

    Prende calendari già costruiti invece delle righe del database: sono gli
    stessi che alimentano le serie e le baseline, e costruirli tre volte
    significava tre occasioni di costruirli in modo leggermente diverso.
    """
    candidates = [
        _week_over_week("Ore di sonno", hours, today, " h", 1),
        _week_over_week("Qualità del sonno", quality, today, "/100"),
        _week_over_week("Body Battery", battery, today, "/100"),
        _week_over_week("FC a riposo", rhr, today, " bpm"),
        _week_over_week("Carico giornaliero", loads, today),
        _after_training("Qualità del sonno", quality, loads, today, "/100"),
        _after_training("FC a riposo", rhr, loads, today, " bpm"),
    ]
    return [c for c in candidates if c]


def _series_line(label: str, by_day: dict, today: date, days: int = SERIES_DAYS,
                 decimals: int = 0) -> str | None:
    """Una riga con gli ultimi `days` giorni, dal più vecchio a oggi.

    Il punto centrale separa la settimana scorsa da quella in corso: aiuta il
    modello a leggere «prima» e «adesso» senza contare le posizioni.

    **Una cella per giorno di calendario**, buchi compresi. Prima queste righe
    venivano da liste di misurazioni esistenti, quindi con tre notti non
    registrate la riga «Sonno» copriva diciassette giorni in quattordici celle
    mentre la riga «Carico» — costruita per data — ne copriva quattordici. Le
    colonne non corrispondevano, e l'intestazione diceva al modello di leggerle
    incolonnate: un sonno finiva accanto al carico di un altro giorno, e la
    correlazione che ne ricavava era inventata.

    `decimals` serve alle ore: arrotondare 7,5 a 8 qui riprodurrebbe, dentro
    la serie, lo stesso equivoco che le unità esplicite hanno appena tolto.
    """
    window = [by_day.get(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    if not any(v is not None for v in window):
        return None

    width = 3 + (decimals + 1 if decimals else 0)
    cells = [("—" if v is None else _n(float(v), decimals)).rjust(width) for v in window]
    half = len(cells) // 2
    body = " ".join(cells[:half]) + "  ·  " + " ".join(cells[half:])
    return f"{label:<14}{body}"


# ============================================================================
# Allenamenti
# ============================================================================


def _pace(activity) -> str:
    """Ritmo o velocità, secondo lo sport.

    La scelta sta in `app.sports`, un posto solo: un giro in bici «a 2:35/km»
    è aritmeticamente giusto e illeggibile, e il modello non ragiona meglio di
    un ciclista.
    """
    from app.sports import speed_label

    value = speed_label(activity)
    return f" a {value}" if value else ""


def _zone_summary(zones: list | None) -> str:
    """I minuti per zona di una singola attività, solo quelli non nulli."""
    if not zones:
        return ""
    parts = [
        f"Z{i + 1} {round(seconds / 60)}'"
        for i, seconds in enumerate(zones)
        if seconds and seconds >= 60
    ]
    return " · " + " ".join(parts) if parts else ""


def activity_lines(activities: list, limit: int = RECENT_ACTIVITIES) -> list[str]:
    """Gli ultimi allenamenti, una riga ciascuno.

    Sono i dati più rilevanti che esistano per un coach, e prima non erano nel
    contesto: il modello doveva chiederli con un tool, e quindi spesso
    rispondeva senza.
    """
    from app.sports import label_of

    lines = []
    for activity in activities[:limit]:
        when = activity.start_time.strftime("%d/%m") if activity.start_time else "—"
        km = (activity.distance_m or 0) / 1000
        minutes = (activity.duration_sec or 0) / 60

        pieces = [when, label_of(activity)]
        if km >= 0.1:
            pieces.append(f"{km:.1f} km".replace(".", ","))
        if minutes >= 1:
            pieces.append(f"{round(minutes)}'")

        text = " · ".join(pieces) + _pace(activity)
        if activity.avg_hr:
            text += f" · FC {round(activity.avg_hr)}"
            if activity.max_hr:
                text += f"/{round(activity.max_hr)}"
        text += _zone_summary(activity.hr_zones_json)
        lines.append(text)
    return lines


# ============================================================================
# Il soggettivo e il piano
# ============================================================================

CHECKIN_LABELS = {
    "energy": "energia",
    "legs": "gambe",
    "mood": "testa",
    "sleep_quality": "sonno percepito",
}


def _checkin_lines(db, user, today: date) -> str:
    """Le risposte del mattino, di oggi e la media della settimana.

    La media accanto al valore di oggi è quello che rende la riga utile: «gambe
    2/5» da solo è una giornata, «2/5 contro una media di 4» è un segnale.
    """
    from sqlalchemy import select

    from app.db.models import DailyCheckin

    rows = list(db.scalars(
        select(DailyCheckin)
        .where(
            DailyCheckin.user_id == user.id,
            DailyCheckin.day <= today,
            DailyCheckin.day > today - timedelta(days=BASELINE_DAYS),
        )
        .order_by(DailyCheckin.day)
    ).all())
    if not rows:
        return ""

    latest = rows[-1]
    age = (today - latest.day).days
    if age > 2:
        return ""

    quando = "oggi" if age == 0 else ("ieri" if age == 1 else f"{age} giorni fa")
    week = [r for r in rows if r.day > today - timedelta(days=7)]

    parts = []
    for field, label in CHECKIN_LABELS.items():
        value = getattr(latest, field, None)
        if value is None:
            continue
        recent = [getattr(r, field) for r in week if getattr(r, field) is not None]
        media = f", media settimana {sum(recent) / len(recent):.1f}".replace(".", ",") \
            if len(recent) >= 3 else ""
        parts.append(f"{label} {value}/5{media}")

    if not parts:
        return ""

    line = f"Check-in di {quando}: " + "; ".join(parts) + "."
    if latest.note:
        line += f" Ha scritto: «{latest.note.strip()}»"
    return line


def _plan_lines(db, user, today: date) -> str:
    """Il piano attivo e quanto lo sta seguendo.

    Prima il modello non sapeva che esistesse un piano, né che l'atleta ne
    avesse saltate tre sedute su quattro: consigliava la giornata come se
    nessun programma fosse in corso.
    """
    from app import queries as q
    from app.training import adherence, current_week_number, get_active_plan

    plan = get_active_plan(db, user.id)
    if plan is None:
        return ""

    week = current_week_number(plan, today)
    lines = [
        f"Piano «{plan.plan_json.get('title', 'senza titolo')}», "
        f"settimana {week} di {plan.weeks_total}."
    ]

    done = adherence(plan, q.recent_activities(db, user.id, 200, end_date=today), today)
    line = done.as_line()
    if line:
        lines.append("Aderenza: " + line + ".")
    if done.is_adrift:
        lines.append(
            "Il piano non descrive più quello che sta succedendo: valutane uno nuovo."
        )
    return "\n".join(lines)


# ============================================================================
# Il briefing completo
# ============================================================================


def build(db: Session, user: User, today: date | None = None) -> str:
    """Il testo che finisce nel prompt. Circa 600 token."""
    today = today or today_for(user)

    # `end_date` su tutte: senza, chiedere il briefing di una data passata
    # restituiva anche i giorni successivi, e il coaching di martedì scritto
    # oggi sapeva com'era finita la settimana.
    wellness = q.wellness_series(db, user.id, BASELINE_DAYS, end_date=today)
    sleep = q.sleep_series(db, user.id, BASELINE_DAYS, end_date=today)
    training = q.training_series(db, user.id, BASELINE_DAYS, end_date=today)
    activities = q.recent_activities(db, user.id, RECENT_ACTIVITIES, end_date=today)

    # Da qui in poi si ragiona per **calendario**, non per elenco di
    # misurazioni: è la differenza fra righe incolonnate e righe che sembrano
    # incolonnate.
    hours = {d: v / 3600 for d, v in _by_day(sleep, "total_sleep_sec").items()}
    quality = _by_day(sleep, "sleep_score")
    battery = _by_day(wellness, "body_battery_high")
    rhr = _by_day(wellness, "resting_hr")
    stress = _by_day(wellness, "avg_stress")

    from app.analysis import cache as analysis_cache

    load = analysis_cache.load_summary(db, user, today)
    profile = load.profile

    blocks: list[str] = []

    # --- profilo e soglie ---
    from app.sports import primary_label

    identity = [f"si allena soprattutto in {primary_label(user).lower()}"]
    if user.birth_year:
        identity.append(f"{today.year - user.birth_year} anni")
    identity.append(
        f"FC max {profile.hr_max} ({profile.source_of('hr_max')})"
    )
    identity.append(f"FC riposo {profile.hr_rest} ({profile.source_of('hr_rest')})")
    identity.append(f"soglia {profile.lthr} ({profile.source_of('lthr')})")
    if profile.ftp:
        identity.append(f"FTP {profile.ftp} W")
    blocks.append("PROFILO\n" + ", ".join(identity) + ".")

    # --- oggi contro il proprio normale ---
    #
    # Le unità sono esplicite su **ogni** riga, e non è pignoleria: mandando
    # «Sonno 41» il modello ha scritto «41 minuti di sonno» a un atleta che
    # aveva dormito sette ore. Un punteggio senza scala è un numero che chiede
    # di essere interpretato, e un modello interpreta sempre.
    #
    # Le ore dormite sono qui per la stessa ragione: il punteggio dice *com'è
    # andata*, non *quanto*, e senza le ore la domanda «ho dormito poco?» non
    # ha una risposta nei dati che gli passiamo.
    baselines = [
        build_baseline("Sonno (ore)", hours, today,
                       higher_is_better=True, unit=" h", decimals=1),
        build_baseline("Qualità sonno", quality, today,
                       higher_is_better=True, unit="/100"),
        build_baseline("Body Battery", battery, today,
                       higher_is_better=True, unit="/100"),
        build_baseline("FC riposo", rhr, today,
                       higher_is_better=False, unit=" bpm"),
        build_baseline("Stress", stress, today,
                       higher_is_better=False, unit="/100"),
    ]
    measured = [b for b in baselines if b.today is not None]
    if measured:
        blocks.append(
            "OGGI CONTRO IL TUO NORMALE (ultimi 30 giorni)\n"
            + "\n".join(b.as_line() for b in measured)
        )

    # `measured` e non `blocks`: senza questa condizione, per un atleta la cui
    # sorgente non misura il benessere l'HRV finirebbe appiccicato in coda al
    # blocco del profilo, che è tutt'altra cosa.
    hrv_status = q.latest(training, "hrv_status")
    if hrv_status and measured:
        from app.insights import hrv_status_label

        blocks[-1] += f"\nHRV {hrv_status_label(hrv_status)}"

    # --- le serie ---
    series = [
        _series_line("Sonno h", hours, today, decimals=1),
        _series_line("Qualità /100", quality, today),
        _series_line("Battery /100", battery, today),
        _series_line("FC riposo bpm", rhr, today),
        _series_line("Stress /100", stress, today),
        _series_line("Carico", load.daily, today) if load.daily else None,
    ]
    present = [s for s in series if s]
    if present:
        blocks.append(
            f"ANDAMENTO {SERIES_DAYS} GIORNI (una colonna per giorno, dal più "
            "vecchio a oggi; le righe sono allineate fra loro, «—» vuol dire "
            "non misurato; il punto separa le due settimane; l'unità è nel "
            "nome della riga)\n"
            + "\n".join(present)
        )

    # --- le relazioni, già calcolate ---
    relations = relation_lines(hours, quality, battery, rhr, load.daily, today)
    if relations:
        blocks.append(
            "CONFRONTI GIÀ CALCOLATI (usali: non ricavarli a occhio dalle serie)\n"
            + "\n".join(f"- {r}" for r in relations)
        )

    # --- forma ---
    if load.has_data:
        form_text, _ = load.form_reading
        acwr_text, _ = load.acwr_reading
        lines = [
            f"Fitness {_n(load.ctl)} · Fatica {_n(load.atl)} · "
            f"Forma {_n(load.tsb)} ({form_text})",
            f"Carico ultimi 7 giorni {_n(load.weekly_load)}, "
            f"7 giorni prima {_n(load.previous_weekly_load)}",
        ]
        if load.acwr is not None:
            lines.append(f"Rapporto acuto/cronico {load.acwr:.2f} ({acwr_text})"
                         .replace(".", ","))
        else:
            lines.append(
                "Rapporto acuto/cronico non calcolabile: servono almeno otto "
                "giorni di allenamento in quattro settimane"
            )
        if load.zones.is_readable:
            pct = load.zones.percentages
            lines.append(
                "Tempo per zona: "
                + " ".join(f"Z{i + 1} {p:.0f}%" for i, p in enumerate(pct))
            )
        if not load.is_reliable:
            lines.append(
                "Attenzione: parte degli allenamenti non ha la frequenza cardiaca, "
                "quindi il carico è in parte stimato dalla durata"
            )
        blocks.append("FORMA E CARICO\n" + "\n".join(lines))

    # --- come sta, secondo lui ---
    #
    # È l'unico dato che non viene da un apparecchio, e il modello deve poterlo
    # distinguere dagli altri: una FC a riposo è un fatto, «gambe 2/5» è un
    # giudizio — che però batte qualunque sensore nel dire se domani conviene
    # spingere.
    checkin_block = _checkin_lines(db, user, today)
    if checkin_block:
        blocks.append("COME DICE DI STARE (risposte sue, non misurazioni)\n" + checkin_block)

    # --- il piano, e se lo sta seguendo ---
    plan_block = _plan_lines(db, user, today)
    if plan_block:
        blocks.append("IL PIANO\n" + plan_block)

    # --- allenamenti ---
    lines = activity_lines(activities)
    if lines:
        blocks.append(
            f"ULTIMI {len(lines)} ALLENAMENTI (il più recente per primo)\n"
            + "\n".join(lines)
        )
    else:
        blocks.append("ULTIMI ALLENAMENTI\nNessuno registrato.")

    # --- VO2max, che si muove lentamente e va letto sul mese ---
    vo2_now = q.latest(training, "vo2max")
    if vo2_now is not None:
        vo2_values = _clean([t.vo2max for t in training])
        line = f"VO2max {vo2_now:.1f}".replace(".", ",")
        if len(vo2_values) >= MIN_POINTS_FOR_BASELINE:
            delta = vo2_now - vo2_values[0]
            verso = "in salita" if delta > 0.3 else ("in calo" if delta < -0.3 else "stabile")
            line += f" ({verso} sul mese, {delta:+.1f})".replace(".", ",")
        blocks.append("MOTORE AEROBICO\n" + line)

    return "\n\n".join(blocks)
