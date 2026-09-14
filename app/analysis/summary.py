"""Il quadro del carico di un utente, pronto da mostrare o da dare all'AI.

Mette insieme profilo, attività e formule in un oggetto solo, perché le pagine,
il motore di prontezza, le regole di notifica e i tool della chat facciano tutti
riferimento agli stessi numeri. Se il carico lo calcolassero in tre posti
diversi, prima o poi direbbero tre cose diverse.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis import load as ld
from app.analysis.profile import AthleteProfile, resolve_profile
from app.analysis.zones import ZoneDistribution, zone_distribution
from app.clock import today_for
from app.db.models import Activity, User

# La fitness è una media mobile a 42 giorni: per non partire da un valore
# arbitrario serve almeno il triplo di storico.
HISTORY_DAYS = 180

# Sotto questa quota di attività con un carico misurato (potenza o FC) i numeri
# sono più stima che misura, e l'interfaccia deve dirlo.
RELIABLE_MEASURED_FRACTION = 0.6


@dataclass
class LoadSummary:
    """Tutto quello che si può dire sul carico, in un colpo solo."""

    profile: AthleteProfile
    daily: dict[date, float]
    pmc: list[ld.PmcPoint]
    zones: ZoneDistribution
    day: date
    activities_total: int = 0
    activities_measured: int = 0
    sources: dict[str, int] = field(default_factory=dict)

    # --- i tre numeri del Performance Management Chart ---

    @property
    def _last(self) -> ld.PmcPoint | None:
        return self.pmc[-1] if self.pmc else None

    @property
    def ctl(self) -> float | None:
        """Fitness: il lavoro che il corpo ha assorbito nelle ultime settimane."""
        return self._last.ctl if self._last else None

    @property
    def atl(self) -> float | None:
        """Fatica: il lavoro degli ultimi giorni, non ancora smaltito."""
        return self._last.atl if self._last else None

    @property
    def tsb(self) -> float | None:
        """Forma: quanta fitness è effettivamente disponibile oggi."""
        return self._last.tsb if self._last else None

    # --- i segnali di allarme ---

    @property
    def acwr(self) -> float | None:
        return ld.acwr(self.daily, self.day)

    @property
    def monotony(self) -> float | None:
        return ld.monotony(self.daily, self.day)

    @property
    def strain(self) -> float | None:
        return ld.strain(self.daily, self.day)

    @property
    def weekly_load(self) -> float:
        return sum(
            self.daily.get(self.day - timedelta(days=i), 0.0)
            for i in range(ld.ACUTE_DAYS)
        )

    @property
    def previous_weekly_load(self) -> float:
        return sum(
            self.daily.get(self.day - timedelta(days=i), 0.0)
            for i in range(ld.ACUTE_DAYS, ld.ACUTE_DAYS * 2)
        )

    # --- quanto fidarsi di questi numeri ---

    @property
    def measured_fraction(self) -> float:
        if not self.activities_total:
            return 0.0
        return self.activities_measured / self.activities_total

    @property
    def is_reliable(self) -> bool:
        return (
            bool(self.daily)
            and self.measured_fraction >= RELIABLE_MEASURED_FRACTION
        )

    @property
    def has_data(self) -> bool:
        return bool(self.daily)

    @property
    def main_source(self) -> str | None:
        """Da cosa viene la maggior parte dei carichi: potenza, FC o durata."""
        if not self.sources:
            return None
        return max(self.sources, key=lambda k: self.sources[k])

    # --- letture pronte ---

    @property
    def acwr_reading(self) -> tuple[str, str]:
        return ld.acwr_label(self.acwr)

    @property
    def form_reading(self) -> tuple[str, str]:
        return ld.form_label(self.tsb)

    def chart(self, days: int = 90) -> dict:
        """Serie pronte per Chart.js, limitate agli ultimi `days` giorni."""
        window = self.pmc[-days:]
        return {
            "labels": [p.day.strftime("%d/%m") for p in window],
            "ctl": [round(p.ctl, 1) for p in window],
            "atl": [round(p.atl, 1) for p in window],
            "tsb": [round(p.tsb, 1) for p in window],
            "load": [round(p.load, 1) for p in window],
        }


def build(db: Session, user: User, today: date | None = None,
          history_days: int = HISTORY_DAYS) -> LoadSummary:
    """Calcola il quadro del carico leggendo le attività dell'utente."""
    today = today or today_for(user)
    start = today - timedelta(days=history_days)

    activities = list(db.scalars(
        select(Activity)
        .where(
            Activity.user_id == user.id,
            Activity.start_time >= datetime.combine(start, time.min),
        )
        .order_by(Activity.start_time.desc())
    ).all())

    # Passa dalla cache di richiesta: `resolve_profile` interroga un anno di
    # attività per il picco di FC, e il profilo lo vogliono anche le pagine.
    from app.analysis import cache as analysis_cache

    profile = analysis_cache.profile(db, user, today)

    measured = 0
    sources: dict[str, int] = {}
    for activity in activities:
        single = ld.activity_load(activity, profile)
        if single is None:
            continue
        sources[single.source] = sources.get(single.source, 0) + 1
        if single.is_measured:
            measured += 1

    daily = ld.daily_loads(activities, profile)

    return LoadSummary(
        profile=profile,
        daily=daily,
        pmc=ld.pmc_series(daily, start, today),
        zones=zone_distribution(activities),
        day=today,
        activities_total=len(activities),
        activities_measured=measured,
        sources=sources,
    )
