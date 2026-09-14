"""Le soglie fisiologiche dell'atleta.

Ogni formula sul carico ha bisogno di sapere *rispetto a cosa* misurare uno
sforzo: 150 bpm sono una passeggiata per uno e una gara per un altro. Quelle
soglie sono quattro — FC massima, FC a riposo, soglia anaerobica, FTP — e in
`/settings` l'utente può dichiararle.

Chi non le dichiara non resta senza analisi: qui vengono **stimate dai dati che
l'app ha già**. Ogni valore porta con sé la propria provenienza, così
l'interfaccia può dire onestamente quando un numero è misurato e quando è una
stima — perché un carico calcolato su una FC massima inventata è un carico
inventato, e va detto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, DailyWellness, User

from app.clock import today_for

# Provenienze possibili di una soglia.
DECLARED = "dichiarata"
OBSERVED = "osservata"
ESTIMATED = "stimata"

# Un cardiofrequenzimetro che segna meno di 120 bpm di picco su un allenamento
# non ha misurato uno sforzo: e' rumore, non un massimale.
PLAUSIBLE_HR_MAX = 120

# Fallback quando non si sa nulla: FC massima adulta media, riposo medio.
DEFAULT_HR_MAX = 190
DEFAULT_HR_REST = 60

# La soglia anaerobica sta intorno all'88% della FC massima. E' la stima
# classica: buona per iniziare, da correggere con un test quando si può.
LTHR_FRACTION_OF_MAX = 0.88

# Coefficiente esponenziale del TRIMP di Banister: la stessa frazione di
# riserva cardiaca "pesa" diversamente nei due sessi.
TRIMP_K_MALE = 1.92
TRIMP_K_FEMALE = 1.67


@dataclass
class AthleteProfile:
    """Le soglie con cui si normalizza ogni sforzo."""

    hr_max: int
    hr_rest: int
    lthr: int
    ftp: int | None = None
    sex: str | None = None
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def hr_reserve(self) -> int:
        """Riserva cardiaca: l'escursione utile fra riposo e massimale."""
        return max(self.hr_max - self.hr_rest, 1)

    @property
    def trimp_k(self) -> float:
        return TRIMP_K_FEMALE if (self.sex or "").lower().startswith("f") else TRIMP_K_MALE

    @property
    def is_fully_declared(self) -> bool:
        """Vero se nessuna soglia è frutto di una stima."""
        return all(src != ESTIMATED for src in self.sources.values())

    def source_of(self, key: str) -> str:
        return self.sources.get(key, ESTIMATED)


def resolve_profile(db: Session, user: User, today: date | None = None) -> AthleteProfile:
    """Costruisce il profilo: prima il dichiarato, poi l'osservato, poi la stima."""
    today = today or today_for(user)
    sources: dict[str, str] = {}

    hr_max = _resolve_hr_max(db, user, today, sources)
    hr_rest = _resolve_hr_rest(db, user, today, sources)

    # La FC a riposo non può stare sopra la massima: se le stime si incrociano
    # (succede con pochi dati) si tiene la massima e si riporta il riposo sotto.
    if hr_rest >= hr_max:
        hr_rest = max(hr_max - 40, 30)
        sources["hr_rest"] = ESTIMATED

    if user.lthr:
        lthr, sources["lthr"] = int(user.lthr), DECLARED
    else:
        lthr, sources["lthr"] = round(hr_max * LTHR_FRACTION_OF_MAX), ESTIMATED

    ftp = int(user.ftp) if user.ftp else None
    sources["ftp"] = DECLARED if ftp else ESTIMATED

    sources["sex"] = DECLARED if user.sex else ESTIMATED

    return AthleteProfile(
        hr_max=hr_max, hr_rest=hr_rest, lthr=lthr, ftp=ftp,
        sex=user.sex, sources=sources,
    )


def _resolve_hr_max(db: Session, user: User, today: date, sources: dict) -> int:
    if user.hr_max:
        sources["hr_max"] = DECLARED
        return int(user.hr_max)

    observed = _observed_hr_max(db, user.id, today)
    if observed:
        sources["hr_max"] = OBSERVED
        return observed

    sources["hr_max"] = ESTIMATED
    return _age_predicted_hr_max(user.birth_year, today)


def _resolve_hr_rest(db: Session, user: User, today: date, sources: dict) -> int:
    if user.hr_rest:
        sources["hr_rest"] = DECLARED
        return int(user.hr_rest)

    observed = _observed_hr_rest(db, user.id, today)
    if observed:
        sources["hr_rest"] = OBSERVED
        return observed

    sources["hr_rest"] = ESTIMATED
    return DEFAULT_HR_REST


def _observed_hr_max(db: Session, user_id: int, today: date) -> int | None:
    """Il picco più alto registrato nell'ultimo anno di allenamenti.

    È il dato migliore che esista senza un test massimale: una FC che il cuore
    ha davvero raggiunto batte qualunque formula sull'età.
    """
    since = datetime.combine(today - timedelta(days=365), time.min)
    peaks = db.scalars(
        select(Activity.max_hr).where(
            Activity.user_id == user_id,
            Activity.max_hr.isnot(None),
            Activity.start_time >= since,
        )
    ).all()
    values = [int(p) for p in peaks if p and p >= PLAUSIBLE_HR_MAX]
    return max(values) if values else None


def _observed_hr_rest(db: Session, user_id: int, today: date) -> int | None:
    """La mediana della FC a riposo degli ultimi 60 giorni.

    Mediana e non media: una sola notte con l'orologio slacciato produce un
    valore assurdo che trascinerebbe la media.
    """
    since = today - timedelta(days=60)
    rows = db.scalars(
        select(DailyWellness.resting_hr).where(
            DailyWellness.user_id == user_id,
            DailyWellness.day >= since,
            DailyWellness.resting_hr.isnot(None),
        )
    ).all()
    values = [float(r) for r in rows if r]
    return round(median(values)) if values else None


def _age_predicted_hr_max(birth_year: int | None, today: date) -> int:
    """Formula di Nes: 211 − 0,64 × età.

    Meno grossolana della classica 220 − età, che sovrastima i giovani e
    sottostima gli over 40. Senza anno di nascita si torna al valore medio.
    """
    if not birth_year:
        return DEFAULT_HR_MAX
    age = today.year - int(birth_year)
    if not 10 <= age <= 100:
        return DEFAULT_HR_MAX
    return round(211 - 0.64 * age)
