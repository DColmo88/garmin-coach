# Coach Page + Design System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `/coach` landing page in a blended Oura/Whoop dark style, backed by a deterministic readiness + insights + coaching engine and a reusable dark design system.

**Architecture:** A pure-Python data layer builds a `coach_snapshot` dict from the SQLite DB; three pure modules (`readiness`, `insights`, `coaching`) compute results from that snapshot with zero network calls; a `/coach` FastAPI route renders `coach.html` using a reusable SVG-ring macro and dark CSS components. The coach message goes through a `coach()` seam on the existing `AIProvider` so a real Claude provider can be dropped in later.

**Tech Stack:** FastAPI, Jinja2, SQLAlchemy 2.0, vanilla JS, inline SVG (no Chart.js for rings), pytest.

## Global Constraints

- Python **3.11+** only (garminconnect lib requires it); run everything from the venv (`./venv/bin/python`, `./venv/bin/pytest`, `./venv/bin/uvicorn`).
- All data access is **read-only and None-safe**: missing fields become `None`, never raise. No page may return HTTP 500 on sparse/empty DB.
- No new heavy dependencies: only add `pytest` to `requirements.txt`. No frontend framework — Jinja2 + vanilla JS only.
- **No network/AI calls** in this cycle: the coach message is deterministic Python.
- UI text in **Italian** (matches existing app). Code/comments may follow existing bilingual style.
- Design tokens (verbatim): `--bg:#0c0e14`, `--surface:#161922`, `--surface-2:#1d212c`, accent teal `#00d4aa`, amber `#f5a623`. Score bands: green `≥80`, amber `50–79`, red `<50`.
- Work happens on branch `feature/coach-page-design-system` (already created).

---

### Task 1: `coach_snapshot` data layer

**Files:**
- Modify: `app/queries.py` (append new function + small helpers)
- Test: `tests/test_coach_snapshot.py`

**Interfaces:**
- Consumes: `app.db.database.Base`, models `Activity, SleepRecord, TrainingMetric, DailyWellness` (existing).
- Produces: `coach_snapshot(db: Session) -> dict` returning exactly these keys (all values `float | int | str | None` unless noted):
  ```
  sleep_score, sleep_score_7d_avg, deep_pct,
  hrv_status, hrv_weekly_avg,
  body_battery_high, body_battery_low_7d_avg,
  load_ratio,                      # ACWR: avg daily load 7d / avg daily load 28d
  resting_hr_latest, resting_hr_7d_avg, resting_hr_30d_avg,
  vo2max_latest, vo2max_4w_ago,
  days_since_last_run,             # int | None
  consecutive_active_days,         # int (>=0, never None)
  steps_latest, step_goal,
  avg_stress_latest,
  has_data                         # bool: any wellness/sleep/training row exists
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coach_snapshot.py
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Activity, DailyWellness, SleepRecord, TrainingMetric
from app.queries import coach_snapshot


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_snapshot_empty_db_is_safe():
    db = _session()
    snap = coach_snapshot(db)
    assert snap["has_data"] is False
    assert snap["sleep_score"] is None
    assert snap["consecutive_active_days"] == 0
    assert snap["days_since_last_run"] is None


def test_snapshot_computes_latest_and_trends():
    db = _session()
    today = date(2026, 7, 1)
    # 30 days of wellness: resting_hr trending down (older higher)
    for i in range(30):
        db.add(DailyWellness(
            day=today - timedelta(days=i),
            total_steps=8000, step_goal=10000,
            resting_hr=60 if i < 7 else 64,
            body_battery_high=90, body_battery_low=20,
            avg_stress=35,
        ))
    db.add(SleepRecord(day=today, total_sleep_sec=7*3600,
                       deep_sleep_sec=3600, sleep_score=78))
    db.add(TrainingMetric(day=today, vo2max=50, training_load=200,
                          hrv_weekly_avg=65, hrv_status="balanced"))
    db.commit()

    snap = coach_snapshot(db)
    assert snap["has_data"] is True
    assert snap["sleep_score"] == 78
    assert snap["resting_hr_latest"] == 60
    assert snap["resting_hr_7d_avg"] == 60          # last 7 days all 60
    assert round(snap["deep_pct"], 2) == 0.14        # 3600 / 25200
    assert snap["hrv_status"] == "balanced"
    assert snap["step_goal"] == 10000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/test_coach_snapshot.py -v`
Expected: FAIL with `ImportError: cannot import name 'coach_snapshot'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/queries.py` (uses existing `select`, `Session`, models, and the existing `wellness_series`/`sleep_series`/`training_series`/`recent_activities` helpers):

```python
from datetime import date, timedelta


def _avg(nums: list) -> float | None:
    vals = [n for n in nums if n is not None]
    return sum(vals) / len(vals) if vals else None


def _is_run(activity_type: str | None) -> bool:
    return bool(activity_type) and "run" in activity_type.lower()


def coach_snapshot(db: Session) -> dict:
    """Costruisce lo snapshot deterministico per readiness/insights/coaching.

    Tutto None-safe: campi mancanti -> None, mai eccezioni.
    """
    wellness = wellness_series(db, 30)          # crescente
    sleep = sleep_series(db, 30)
    training = training_series(db, 30)
    acts = recent_activities(db, 50)            # desc per start_time

    rhr_all = [w.resting_hr for w in wellness]
    rhr_7 = [w.resting_hr for w in wellness[-7:]]
    load_7 = [t.training_load for t in training[-7:]]
    load_28 = [t.training_load for t in training[-28:]]
    avg_load_7 = _avg(load_7)
    avg_load_28 = _avg(load_28)
    load_ratio = (avg_load_7 / avg_load_28) if (avg_load_7 and avg_load_28) else None

    latest_sleep = sleep[-1] if sleep else None
    deep_pct = None
    if latest_sleep and latest_sleep.total_sleep_sec and latest_sleep.deep_sleep_sec:
        deep_pct = latest_sleep.deep_sleep_sec / latest_sleep.total_sleep_sec

    vo2_now = latest(training, "vo2max")
    vo2_4w = None
    if len(training) >= 28:
        vo2_4w = next((t.vo2max for t in training[:-21] if t.vo2max is not None), None)

    # days since last run / consecutive active days (based on activity start dates)
    act_days = sorted({a.start_time.date() for a in acts if a.start_time}, reverse=True)
    run_days = [a.start_time.date() for a in acts if a.start_time and _is_run(a.activity_type)]
    today = date.today()
    days_since_last_run = (today - max(run_days)).days if run_days else None
    consecutive = 0
    cursor = today
    act_day_set = set(act_days)
    while cursor in act_day_set:
        consecutive += 1
        cursor = cursor - timedelta(days=1)

    return {
        "sleep_score": latest(sleep, "sleep_score"),
        "sleep_score_7d_avg": _avg([s.sleep_score for s in sleep[-7:]]),
        "deep_pct": deep_pct,
        "hrv_status": latest(training, "hrv_status"),
        "hrv_weekly_avg": latest(training, "hrv_weekly_avg"),
        "body_battery_high": latest(wellness, "body_battery_high"),
        "body_battery_low_7d_avg": _avg([w.body_battery_low for w in wellness[-7:]]),
        "load_ratio": load_ratio,
        "resting_hr_latest": latest(wellness, "resting_hr"),
        "resting_hr_7d_avg": _avg(rhr_7),
        "resting_hr_30d_avg": _avg(rhr_all),
        "vo2max_latest": vo2_now,
        "vo2max_4w_ago": vo2_4w,
        "days_since_last_run": days_since_last_run,
        "consecutive_active_days": consecutive,
        "steps_latest": latest(wellness, "total_steps"),
        "step_goal": latest(wellness, "step_goal"),
        "avg_stress_latest": latest(wellness, "avg_stress"),
        "has_data": bool(wellness or sleep or training),
    }
```

> Note: `test_snapshot_computes_latest_and_trends` uses `date(2026,7,1)` as "today" for the rows but `consecutive_active_days`/`days_since_last_run` use the real `date.today()`. The asserted fields in the test do not depend on those two, so the test is stable. Do not assert on `consecutive_active_days`/`days_since_last_run` with hard-coded dates here — Task 3 tests those via the pure functions instead.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/test_coach_snapshot.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/queries.py tests/test_coach_snapshot.py
git commit -m "feat: coach_snapshot data layer (None-safe trends)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Readiness engine

**Files:**
- Create: `app/ai/readiness.py`
- Test: `tests/test_readiness.py`

**Interfaces:**
- Consumes: the `coach_snapshot` dict shape from Task 1.
- Produces:
  ```python
  @dataclass
  class ReadinessFactor:
      name: str          # "Sonno" | "HRV" | "Body Battery" | "Carico" | "FC riposo"
      value: int         # 0-100 normalized factor score
      color: str         # "green" | "amber" | "red"

  @dataclass
  class ReadinessResult:
      score: int | None  # None only when every primary signal is missing
      label: str         # e.g. "Buono"
      emoji: str         # e.g. "✅"
      recommendation: str
      breakdown: list[ReadinessFactor]

  def compute_readiness(snap: dict) -> ReadinessResult
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readiness.py
from app.ai.readiness import compute_readiness


def _base() -> dict:
    return {
        "sleep_score": 80, "hrv_status": "balanced", "body_battery_high": 90,
        "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 53,
    }


def test_high_readiness_band():
    r = compute_readiness(_base())
    assert r.score >= 80
    assert r.emoji == "🔥"
    assert r.label == "Pronto"
    assert len(r.breakdown) == 5


def test_all_missing_returns_none_score():
    r = compute_readiness({})
    assert r.score is None
    assert r.label == "Dati assenti"


def test_low_readiness_band():
    snap = {"sleep_score": 30, "hrv_status": "unbalanced", "body_battery_high": 20,
            "load_ratio": 1.8, "resting_hr_7d_avg": 60, "resting_hr_30d_avg": 55}
    r = compute_readiness(snap)
    assert r.score < 50
    assert r.emoji in {"🔵", "❌"}


def test_hrv_missing_uses_neutral_default():
    snap = dict(_base())
    snap["hrv_status"] = None
    hrv_factor = next(f for f in compute_readiness(snap).breakdown if f.name == "HRV")
    assert hrv_factor.value == 65
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/test_readiness.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.readiness'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/ai/readiness.py
"""Readiness Score engine — Python puro, deterministico, zero AI."""
from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {"sleep": 0.30, "hrv": 0.25, "battery": 0.20, "load": 0.15, "rhr": 0.10}


@dataclass
class ReadinessFactor:
    name: str
    value: int
    color: str


@dataclass
class ReadinessResult:
    score: int | None
    label: str
    emoji: str
    recommendation: str
    breakdown: list[ReadinessFactor]


def _color(v: int) -> str:
    if v >= 70:
        return "green"
    if v >= 45:
        return "amber"
    return "red"


def _hrv_factor(status: str | None) -> int:
    if not status:
        return 65
    s = status.lower()
    if "balanc" in s or "good" in s:
        return 100
    if "low" in s or "unbalanc" in s or "poor" in s:
        return 40
    return 65


def _load_factor(ratio: float | None) -> int:
    if ratio is None:
        return 65
    if ratio > 1.5:
        return 30
    if ratio > 1.2:
        return 55
    if ratio < 0.8:
        return 70
    return 80


def _rhr_factor(rhr_7d: float | None, rhr_30d: float | None) -> int:
    if rhr_7d is None or rhr_30d is None:
        return 65
    if rhr_7d <= rhr_30d - 2:
        return 80
    if rhr_7d >= rhr_30d + 2:
        return 40
    return 65


def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _band(score: int) -> tuple[str, str, str]:
    if score >= 80:
        return "🔥", "Pronto", "Allenamento intenso / sessione chiave"
    if score >= 65:
        return "✅", "Buono", "Allenamento moderato"
    if score >= 50:
        return "🟡", "Discreto", "Corsa easy / attività leggera"
    if score >= 35:
        return "🔵", "Stanco", "Recovery run o riposo attivo"
    return "❌", "Riposo", "Riposo completo"


def compute_readiness(snap: dict) -> ReadinessResult:
    sleep = snap.get("sleep_score")
    battery = snap.get("body_battery_high")
    primary_present = any(v is not None for v in (
        sleep, snap.get("hrv_status"), battery,
        snap.get("load_ratio"), snap.get("resting_hr_7d_avg"),
    ))

    f_sleep = _clamp(sleep) if sleep is not None else 65
    f_hrv = _hrv_factor(snap.get("hrv_status"))
    f_batt = _clamp(battery) if battery is not None else 65
    f_load = _load_factor(snap.get("load_ratio"))
    f_rhr = _rhr_factor(snap.get("resting_hr_7d_avg"), snap.get("resting_hr_30d_avg"))

    breakdown = [
        ReadinessFactor("Sonno", f_sleep, _color(f_sleep)),
        ReadinessFactor("HRV", f_hrv, _color(f_hrv)),
        ReadinessFactor("Body Battery", f_batt, _color(f_batt)),
        ReadinessFactor("Carico", f_load, _color(f_load)),
        ReadinessFactor("FC riposo", f_rhr, _color(f_rhr)),
    ]

    if not primary_present:
        return ReadinessResult(None, "Dati assenti", "—",
                               "Premi Sincronizza per scaricare i tuoi dati Garmin.",
                               breakdown)

    raw = (f_sleep * WEIGHTS["sleep"] + f_hrv * WEIGHTS["hrv"]
           + f_batt * WEIGHTS["battery"] + f_load * WEIGHTS["load"]
           + f_rhr * WEIGHTS["rhr"])
    score = _clamp(raw)
    emoji, label, rec = _band(score)
    return ReadinessResult(score, label, emoji, rec, breakdown)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/test_readiness.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ai/readiness.py tests/test_readiness.py
git commit -m "feat: deterministic readiness engine

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Insights engine

**Files:**
- Create: `app/ai/insights.py`
- Test: `tests/test_insights.py`

**Interfaces:**
- Consumes: the `coach_snapshot` dict shape from Task 1.
- Produces:
  ```python
  @dataclass
  class Insight:
      icon: str
      title: str
      text: str
      color: str    # "green" | "amber" | "red"
      priority: int # red=0, amber=1, green=2 (lower = shown first)

  def detect_insights(snap: dict) -> list[Insight]   # all firing rules
  def top_insights(snap: dict, n: int = 3) -> list[Insight]  # sorted, capped
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_insights.py
from app.ai.insights import detect_insights, top_insights


def test_resting_hr_drop_fires_green():
    snap = {"resting_hr_7d_avg": 50, "resting_hr_30d_avg": 55}
    titles = [i.title for i in detect_insights(snap)]
    assert any("FC riposo" in t for t in titles)


def test_load_spike_fires_red():
    snap = {"load_ratio": 1.5}
    found = [i for i in detect_insights(snap) if i.color == "red"]
    assert found and "overtraining" in found[0].text.lower()


def test_days_since_run_fires():
    snap = {"days_since_last_run": 6}
    assert any("senza corsa" in i.text for i in detect_insights(snap))


def test_top_insights_orders_red_first_and_caps():
    snap = {
        "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 55,   # green
        "load_ratio": 1.6,                                    # red
        "sleep_score_7d_avg": 60,                             # amber
        "consecutive_active_days": 8,                         # green
    }
    top = top_insights(snap, n=3)
    assert len(top) == 3
    assert top[0].color == "red"


def test_no_data_yields_empty():
    assert detect_insights({}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/test_insights.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.insights'`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/ai/insights.py
"""Insight rules engine — Python puro, zero AI, zero latenza."""
from __future__ import annotations

from dataclasses import dataclass

_PRIORITY = {"red": 0, "amber": 1, "green": 2}


@dataclass
class Insight:
    icon: str
    title: str
    text: str
    color: str
    priority: int


def _mk(icon: str, title: str, text: str, color: str) -> Insight:
    return Insight(icon, title, text, color, _PRIORITY[color])


def detect_insights(snap: dict) -> list[Insight]:
    out: list[Insight] = []

    rhr7, rhr30 = snap.get("resting_hr_7d_avg"), snap.get("resting_hr_30d_avg")
    if rhr7 is not None and rhr30 is not None:
        if rhr7 <= rhr30 - 3:
            out.append(_mk("📈", "FC riposo in calo",
                           f"FC a riposo {round(rhr30 - rhr7)} bpm più bassa del mese scorso "
                           "— fitness in crescita.", "green"))
        elif rhr7 >= rhr30 + 4:
            out.append(_mk("⚠️", "FC riposo in salita",
                           "FC a riposo più alta del solito — possibile stanchezza o stress.",
                           "amber"))

    ratio = snap.get("load_ratio")
    if ratio is not None and ratio >= 1.4:
        out.append(_mk("⚠️", "Carico acuto alto",
                       "Carico acuto alto rispetto al cronico — rischio overtraining, "
                       "valuta un giorno easy o di riposo.", "red"))

    ss7 = snap.get("sleep_score_7d_avg")
    if ss7 is not None and ss7 < 65:
        out.append(_mk("🌙", "Sonno sotto la media",
                       "Qualità del sonno bassa questa settimana — priorità al recupero.",
                       "amber"))

    vo2_now, vo2_old = snap.get("vo2max_latest"), snap.get("vo2max_4w_ago")
    if vo2_now is not None and vo2_old is not None and vo2_now > vo2_old:
        out.append(_mk("📈", "VO₂max in crescita",
                       f"VO₂max +{round(vo2_now - vo2_old, 1)} rispetto a un mese fa "
                       "— sei più forte.", "green"))

    dslr = snap.get("days_since_last_run")
    if dslr is not None and dslr > 5:
        out.append(_mk("🟢", "Corpo riposato",
                       f"{dslr} giorni senza corsa — il corpo è riposato, momento perfetto "
                       "per ripartire.", "green"))

    streak = snap.get("consecutive_active_days") or 0
    if streak >= 7:
        out.append(_mk("🔥", "In serie",
                       f"{streak} giorni consecutivi attivi — continua così!", "green"))

    deep = snap.get("deep_pct")
    if deep is not None and deep < 0.15:
        out.append(_mk("🌙", "Sonno profondo basso",
                       "Sonno profondo sotto il 15% — prova un rituale serale più regolare.",
                       "amber"))

    bb_low = snap.get("body_battery_low_7d_avg")
    if bb_low is not None and bb_low < 20:
        out.append(_mk("🔋", "Energia a terra",
                       "Body Battery quasi a zero ogni sera — forse stai facendo troppo.",
                       "amber"))

    return out


def top_insights(snap: dict, n: int = 3) -> list[Insight]:
    ordered = sorted(detect_insights(snap), key=lambda i: i.priority)
    return ordered[:n]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/test_insights.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ai/insights.py tests/test_insights.py
git commit -m "feat: deterministic insights engine

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Coaching message + workout + provider seam

**Files:**
- Create: `app/ai/coaching.py`
- Modify: `app/ai/base.py` (add concrete `coach()` default method)
- Test: `tests/test_coaching.py`

**Interfaces:**
- Consumes: `coach_snapshot` dict (Task 1), `ReadinessResult` (Task 2).
- Produces:
  ```python
  @dataclass
  class WorkoutSuggestion:
      icon: str; type: str; duration: str; hr_zone: str; note: str

  @dataclass
  class CoachOutput:
      message: str
      workout: WorkoutSuggestion

  def build_coach_output(snap: dict, readiness: ReadinessResult) -> CoachOutput
  ```
- Adds `AIProvider.coach(self, snap, readiness) -> CoachOutput` (concrete, delegates to `build_coach_output`; future providers may override).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coaching.py
from app.ai.coaching import CoachOutput, build_coach_output
from app.ai.readiness import compute_readiness


def test_output_has_message_and_workout():
    snap = {"sleep_score": 80, "hrv_status": "balanced", "body_battery_high": 90,
            "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 53}
    out = build_coach_output(snap, compute_readiness(snap))
    assert isinstance(out, CoachOutput)
    assert out.message and isinstance(out.message, str)
    assert out.workout.type and out.workout.hr_zone


def test_high_readiness_suggests_intense_workout():
    snap = {"sleep_score": 90, "hrv_status": "balanced", "body_battery_high": 95,
            "load_ratio": 1.0, "resting_hr_7d_avg": 48, "resting_hr_30d_avg": 52}
    out = build_coach_output(snap, compute_readiness(snap))
    assert "Riposo" not in out.workout.type


def test_no_data_message_prompts_sync():
    out = build_coach_output({}, compute_readiness({}))
    assert "Sincronizza" in out.message


def test_provider_seam_delegates():
    from app.ai.provider import get_provider
    snap = {"sleep_score": 70, "hrv_status": "balanced", "body_battery_high": 80,
            "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 50}
    out = get_provider().coach(snap, compute_readiness(snap))
    assert isinstance(out, CoachOutput)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/test_coaching.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ai.coaching'`.

- [ ] **Step 3: Write minimal implementation**

Create `app/ai/coaching.py`:

```python
# app/ai/coaching.py
"""Coaching message + allenamento suggerito — template deterministici.

Nessuna chiamata di rete. Seam pronto per un futuro ClaudeProvider che
faccia override di AIProvider.coach().
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.readiness import ReadinessResult


@dataclass
class WorkoutSuggestion:
    icon: str
    type: str
    duration: str
    hr_zone: str
    note: str


@dataclass
class CoachOutput:
    message: str
    workout: WorkoutSuggestion


_WORKOUTS = {
    "Pronto": WorkoutSuggestion("🏃", "Sessione chiave", "50–70 min",
                                "Zona 4 — intervalli o tempo",
                                "Sei in forma: oggi spingi sulla qualità."),
    "Buono": WorkoutSuggestion("🏃", "Corsa moderata", "40–55 min",
                               "Zona 2–3 — < 155 bpm",
                               "Buona base: ritmo controllato, senza strafare."),
    "Discreto": WorkoutSuggestion("🏃", "Corsa easy", "30–45 min",
                                  "Zona 2 — < 145 bpm",
                                  "Mantieni la conversazione: oggi costruiamo volume aerobico."),
    "Stanco": WorkoutSuggestion("🚶", "Recovery / camminata", "20–30 min",
                                "Zona 1 — molto leggero",
                                "Recupero attivo: muoviti senza alzare il carico."),
    "Riposo": WorkoutSuggestion("💤", "Riposo", "—", "Nessuno",
                                "Il corpo chiede recupero: oggi riposo completo."),
}

_NO_DATA = WorkoutSuggestion("—", "—", "—", "—",
                             "Sincronizza i dati per ricevere un suggerimento.")


def _message(snap: dict, r: ReadinessResult) -> str:
    if r.score is None:
        return ("Non ho ancora abbastanza dati. Premi Sincronizza per scaricare "
                "le tue metriche Garmin e ricevere il coaching di oggi.")
    parts: list[str] = []
    ss = snap.get("sleep_score")
    if ss is not None:
        parts.append(f"Hai dormito con uno score di {round(ss)}")
    hrv = snap.get("hrv_status")
    if hrv:
        parts.append(f"HRV {hrv.lower()}")
    bb = snap.get("body_battery_high")
    if bb is not None:
        parts.append(f"Body Battery a {round(bb)}")
    detail = ", ".join(parts) if parts else "Ecco il quadro di oggi"
    return (f"Prontezza {r.score}/100 — {r.emoji} {r.label}. "
            f"{detail.capitalize()}. {r.recommendation}.")


def build_coach_output(snap: dict, readiness: ReadinessResult) -> CoachOutput:
    workout = _NO_DATA if readiness.score is None else _WORKOUTS[readiness.label]
    return CoachOutput(message=_message(snap, readiness), workout=workout)
```

Then add the seam to `app/ai/base.py` (append inside the `AIProvider` class, after the existing abstract methods — concrete, not abstract, so existing providers keep working):

```python
    def coach(self, snap: dict, readiness):  # type: ignore[no-untyped-def]
        """Coaching del giorno. Default: template deterministici (no rete).

        Un provider AI reale può fare override per generare prosa con un LLM.
        """
        from app.ai.coaching import build_coach_output

        return build_coach_output(snap, readiness)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/pytest tests/test_coaching.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/ai/coaching.py app/ai/base.py tests/test_coaching.py
git commit -m "feat: deterministic coaching message + provider coach() seam

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Dark design system (CSS tokens + ring macro)

**Files:**
- Modify: `app/static/style.css` (append design-system section; do not remove existing rules)
- Create: `app/templates/_components.html` (Jinja macros)

**Interfaces:**
- Produces a Jinja macro `ring(value, vmax, label, sublabel, size)` importable via
  `{% from "_components.html" import ring %}`, rendering an inline-SVG progress ring.
  `value=None` renders a dash and a neutral track. Color follows score bands
  (green ≥80% of vmax, amber ≥50%, red <50%).
- Produces CSS classes consumed by Task 6: `.coach-hero`, `.ring`, `.ring-mini-row`,
  `.metric-card`, `.monitor-card`, `.insight-card`, `.insight-card.green/.amber/.red`,
  `.pill`, `.pill.green/.amber/.red`, `.progress-bar`.

> No unit test (pure presentation). Verified visually in Task 6.

- [ ] **Step 1: Create the ring macro**

Create `app/templates/_components.html`:

```jinja
{% macro ring(value, vmax=100, label="", sublabel="", size=120) %}
{% set r = (size / 2) - 8 %}
{% set circ = 2 * 3.14159265 * r %}
{% set pct = (value / vmax) if (value is not none and vmax) else 0 %}
{% set pct = 0 if pct < 0 else (1 if pct > 1 else pct) %}
{% set band = 'green' if pct >= 0.8 else ('amber' if pct >= 0.5 else 'red') %}
<div class="ring ring-{{ band }}" style="width:{{ size }}px">
  <svg viewBox="0 0 {{ size }} {{ size }}" width="{{ size }}" height="{{ size }}">
    <circle class="ring-track" cx="{{ size/2 }}" cy="{{ size/2 }}" r="{{ r }}"
            fill="none" stroke-width="8"/>
    <circle class="ring-val" cx="{{ size/2 }}" cy="{{ size/2 }}" r="{{ r }}"
            fill="none" stroke-width="8" stroke-linecap="round"
            stroke-dasharray="{{ circ }}"
            stroke-dashoffset="{{ circ * (1 - pct) }}"
            transform="rotate(-90 {{ size/2 }} {{ size/2 }})"/>
  </svg>
  <div class="ring-center">
    <span class="ring-value">{{ value if value is not none else '—' }}</span>
    {% if label %}<span class="ring-label">{{ label }}</span>{% endif %}
  </div>
  {% if sublabel %}<div class="ring-sub">{{ sublabel }}</div>{% endif %}
</div>
{% endmacro %}
```

- [ ] **Step 2: Append the design-system CSS**

Append to `app/static/style.css`:

```css
/* ===== Coach design system (dark, blend Oura/Whoop) ===== */
:root {
  --bg: #0c0e14;
  --surface: #161922;
  --surface-2: #1d212c;
  --hairline: #262b38;
  --text: #e8ebf2;
  --text-dim: #9aa3b2;
  --accent: #00d4aa;
  --amber: #f5a623;
  --good: #35d07f;
  --warn: #f5a623;
  --bad: #ff5c5c;
}
.coach-hero {
  background: radial-gradient(120% 140% at 50% 0%, #1b2030 0%, var(--surface) 55%);
  border: 1px solid var(--hairline);
  border-radius: 20px;
  padding: 28px;
  display: flex; gap: 28px; align-items: center; flex-wrap: wrap;
}
.coach-hero .hero-text { flex: 1; min-width: 240px; }
.coach-hero .hero-status { font-size: 1.4rem; font-weight: 700; margin: 0 0 8px; }
.coach-hero .hero-msg { color: var(--text-dim); line-height: 1.5; }

.ring { position: relative; display: inline-flex; flex-direction: column;
        align-items: center; }
.ring svg { display: block; }
.ring-track { stroke: var(--hairline); }
.ring-green .ring-val { stroke: var(--good); }
.ring-amber .ring-val { stroke: var(--warn); }
.ring-red   .ring-val { stroke: var(--bad); }
.ring-center { position: absolute; top: calc(50% - 6px); left: 0; right: 0;
               display: flex; flex-direction: column; align-items: center;
               transform: translateY(-50%); }
.ring-value { font-size: 2rem; font-weight: 800; line-height: 1; }
.ring-label { font-size: .7rem; letter-spacing: .08em; text-transform: uppercase;
              color: var(--text-dim); }
.ring-sub { margin-top: 6px; font-size: .8rem; color: var(--text-dim); }

.ring-mini-row { display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }
.ring-mini-row .ring-value { font-size: 1.3rem; }

.metric-card, .monitor-card {
  background: var(--surface); border: 1px solid var(--hairline);
  border-radius: 16px; padding: 16px 18px;
}
.monitor-card .mc-label { font-size: .72rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-dim); }
.monitor-card .mc-value { font-size: 1.4rem; font-weight: 700; margin-top: 4px; }

.insight-card {
  background: var(--surface); border: 1px solid var(--hairline);
  border-left: 4px solid var(--text-dim); border-radius: 14px;
  padding: 14px 16px; margin-bottom: 12px;
}
.insight-card.green { border-left-color: var(--good); }
.insight-card.amber { border-left-color: var(--warn); }
.insight-card.red   { border-left-color: var(--bad); }
.insight-card .ic-title { font-weight: 700; margin: 0 0 4px; }
.insight-card .ic-text { color: var(--text-dim); font-size: .9rem; line-height: 1.45; }

.pill { display: inline-flex; flex-direction: column; align-items: center;
  background: var(--surface-2); border-radius: 10px; padding: 8px 12px;
  min-width: 70px; }
.pill .p-name { font-size: .65rem; color: var(--text-dim);
  text-transform: uppercase; letter-spacing: .05em; }
.pill .p-val { font-weight: 700; }
.pill.green .p-val { color: var(--good); }
.pill.amber .p-val { color: var(--warn); }
.pill.red   .p-val { color: var(--bad); }
.pill-row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }

.progress-bar { height: 8px; border-radius: 6px; background: var(--surface-2);
  overflow: hidden; }
.progress-bar > span { display: block; height: 100%; background: var(--accent); }

.workout-card {
  background: var(--surface-2); border: 1px solid var(--hairline);
  border-radius: 16px; padding: 18px 20px;
}
.workout-card .w-head { display: flex; align-items: baseline;
  justify-content: space-between; }
.workout-card .w-type { font-size: 1.15rem; font-weight: 700; }
.workout-card .w-meta { color: var(--text-dim); font-size: .9rem; margin: 6px 0; }
.workout-card .w-note { color: var(--text); font-style: italic; opacity: .85; }
```

- [ ] **Step 3: Verify CSS/macro load without error**

Run: `./venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; \
env=Environment(loader=FileSystemLoader('app/templates')); \
env.get_template('_components.html'); print('macro OK')"`
Expected: prints `macro OK` (no Jinja syntax error).

- [ ] **Step 4: Commit**

```bash
git add app/static/style.css app/templates/_components.html
git commit -m "feat: dark design system tokens + SVG ring macro

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: `/coach` route, template, and nav link

**Files:**
- Modify: `app/routers/pages.py` (add `/coach` route)
- Create: `app/templates/coach.html`
- Modify: `app/templates/base.html` (add Coach nav item)
- Test: `tests/test_coach_page.py`

**Interfaces:**
- Consumes: `coach_snapshot` (Task 1), `compute_readiness` (Task 2), `top_insights` (Task 3), `get_provider().coach()` (Task 4), `ring` macro + CSS (Task 5).
- Produces: `GET /coach` → 200 HTML; renders hero ring, mini-rings, insights, workout.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_coach_page.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_coach_page_renders_200():
    resp = client.get("/coach")
    assert resp.status_code == 200
    assert "READINESS" in resp.text or "Prontezza" in resp.text


def test_coach_page_has_nav_link():
    resp = client.get("/coach")
    assert 'href="/coach"' in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/pytest tests/test_coach_page.py -v`
Expected: FAIL — `GET /coach` returns 404 (route not defined).

- [ ] **Step 3: Add the route**

Add to `app/routers/pages.py` (after the `_ctx` helper, before the Panoramica section). The imports `compute_readiness`, `top_insights` go at module top alongside existing imports:

```python
# top of file, with the other imports
from app.ai.readiness import compute_readiness
from app.ai.insights import top_insights
from app.ai.provider import get_provider
```

```python
# --------------------------- Coach ---------------------------

@router.get("/coach", response_class=HTMLResponse)
def coach(request: Request, db: Session = Depends(get_session)):
    snap = q.coach_snapshot(db)
    readiness = compute_readiness(snap)
    insights = top_insights(snap, 3)
    coaching = get_provider().coach(snap, readiness)
    mini = [
        {"value": snap.get("sleep_score"), "label": "Sonno"},
        {"value": snap.get("body_battery_high"), "label": "Battery"},
        {"value": round(snap["vo2max_latest"]) if snap.get("vo2max_latest") else None,
         "label": "VO₂max", "vmax": 70},
    ]
    in_range = sum(1 for f in readiness.breakdown if f.color == "green")
    return templates.TemplateResponse(
        "coach.html",
        _ctx(request, "coach", snap=snap, readiness=readiness, insights=insights,
             coaching=coaching, mini=mini, in_range=in_range,
             total_metrics=len(readiness.breakdown)),
    )
```

- [ ] **Step 4: Create the template**

Create `app/templates/coach.html`:

```jinja
{% extends "base.html" %}
{% from "_components.html" import ring %}
{% block title %}Coach · Garmin Connector{% endblock %}
{% block heading %}Coach{% endblock %}
{% block content %}

<section class="coach-hero">
  {{ ring(readiness.score, 100, "Readiness", readiness.label, 160) }}
  <div class="hero-text">
    <p class="hero-status">{{ readiness.emoji }} {{ readiness.label }}</p>
    <p class="hero-msg">{{ coaching.message }}</p>
    <div class="pill-row">
      {% for f in readiness.breakdown %}
        <span class="pill {{ f.color }}">
          <span class="p-name">{{ f.name }}</span>
          <span class="p-val">{{ f.value }}</span>
        </span>
      {% endfor %}
    </div>
  </div>
</section>

<div class="ring-mini-row">
  {% for m in mini %}
    {{ ring(m.value, m.vmax | default(100), m.label, "", 96) }}
  {% endfor %}
</div>

<div class="card-grid" style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">
  <div class="monitor-card" style="flex:1;min-width:180px">
    <div class="mc-label">Health Monitor</div>
    <div class="mc-value">{{ in_range }}/{{ total_metrics }} in range</div>
  </div>
  <div class="monitor-card" style="flex:1;min-width:180px">
    <div class="mc-label">Stress</div>
    <div class="mc-value">
      {{ snap.avg_stress_latest | round | int if snap.avg_stress_latest is not none else '—' }}
    </div>
  </div>
</div>

<h2 style="margin:8px 0 12px">Insight di oggi</h2>
{% if insights %}
  {% for i in insights %}
    <div class="insight-card {{ i.color }}">
      <p class="ic-title">{{ i.icon }} {{ i.title }}</p>
      <p class="ic-text">{{ i.text }}</p>
    </div>
  {% endfor %}
{% else %}
  <p class="hero-msg">Nessun insight rilevante oggi — continua a sincronizzare i dati.</p>
{% endif %}

<h2 style="margin:20px 0 12px">Allenamento suggerito</h2>
<div class="workout-card">
  <div class="w-head">
    <span class="w-type">{{ coaching.workout.icon }} {{ coaching.workout.type }}</span>
    <span class="w-meta">{{ coaching.workout.duration }}</span>
  </div>
  <p class="w-meta">{{ coaching.workout.hr_zone }}</p>
  <p class="w-note">"{{ coaching.workout.note }}"</p>
</div>

{% endblock %}
```

- [ ] **Step 5: Add the nav link**

In `app/templates/base.html`, add Coach as the first nav item. Change the `items` list:

```jinja
      {% set items = [
        ('coach', '/coach', '🎯 Coach'),
        ('overview', '/', '◎ Panoramica'),
        ('activities', '/activities', '🏃 Attività'),
        ('sleep', '/sleep', '🌙 Sonno'),
        ('health', '/health', '❤️ Salute'),
        ('body', '/body', '⚖️ Corpo'),
        ('performance', '/performance', '📈 Performance'),
        ('devices', '/devices', '⌚ Dispositivi'),
        ('ai', '/ai', '🤖 AI Insights'),
      ] %}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./venv/bin/pytest tests/test_coach_page.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Run the full suite**

Run: `./venv/bin/pytest -v`
Expected: ALL pass (coach_snapshot, readiness, insights, coaching, coach_page).

- [ ] **Step 8: Manual visual check against real DB**

Run: `./venv/bin/uvicorn app.main:app --reload`
Open `http://localhost:8000/coach`. Confirm: hero ring shows a number/color, mini-rings render, pills are colored, insights and workout card appear, no console/server errors. Stop the server.

- [ ] **Step 9: Commit**

```bash
git add app/routers/pages.py app/templates/coach.html app/templates/base.html tests/test_coach_page.py
git commit -m "feat: /coach landing page (blend Oura/Whoop) + nav link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Add pytest to requirements

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append pytest + httpx (TestClient dep)**

Add these lines to `requirements.txt`:

```
pytest==8.3.4
httpx==0.28.1
```

- [ ] **Step 2: Verify install in venv**

Run: `./venv/bin/pip install -r requirements.txt && ./venv/bin/pytest -q`
Expected: install succeeds; all tests pass.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add pytest + httpx test deps

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- §2 design system → Task 5 ✓
- §3 readiness engine → Task 2 ✓
- §4 insights engine → Task 3 ✓
- §5 coach message + provider seam → Task 4 ✓
- §6 `/coach` layout + routing + nav → Task 6 ✓
- §7 queries extended → Task 1 ✓
- §8 empty states → readiness `score=None` path (Task 2), `_NO_DATA` workout + sync message (Task 4), ring `—` rendering (Task 5) ✓
- §9 testing + pytest dep → Tasks 1–6 tests + Task 7 ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `coach_snapshot` keys defined in Task 1 are consumed with the same names in Tasks 2–4 and 6. `ReadinessResult.breakdown[].color` ("green/amber/red") reused for `in_range` count and pill classes. `CoachOutput.message`/`.workout` consumed in Task 6 template. `ring(value, vmax, label, sublabel, size)` signature matches all call sites. ✓

**Note on TestClient:** Task 6 imports `from app.main import app`; if `app.main` triggers DB engine creation at import, it uses the existing `data/garmin_connector.db` — the route is None-safe so 200 holds even on empty data. `httpx` (Task 7) is required by `fastapi.testclient`; install it before running Task 6 tests if not already present.
