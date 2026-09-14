"""Notifiche: regole, soglie, deduplica, preferenze, canali."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.ai.readiness import compute_readiness
from app.db.models import NotificationLog, PushSubscription, User
from app.notifications import rules
from app.notifications.channels import CHANNELS, EmailChannel, TelegramChannel, WebPushChannel
from app.notifications.dispatcher import channels_for, deliver, dispatch_for_user
from app.notifications.rules import Notification

CALM = {
    "sleep_score": 82, "sleep_score_7d_avg": 80, "hrv_status": "balanced",
    "body_battery_high": 90, "load_ratio": 1.0,
    "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 51,
}
OVERLOADED = {**CALM, "load_ratio": 1.8}
EXHAUSTED = {
    "sleep_score": 35, "sleep_score_7d_avg": 45, "hrv_status": "unbalanced",
    "body_battery_high": 20, "load_ratio": 1.7,
    "resting_hr_7d_avg": 62, "resting_hr_30d_avg": 54,
}
PEAKING = {
    "sleep_score": 95, "sleep_score_7d_avg": 90, "hrv_status": "balanced",
    "body_battery_high": 98, "load_ratio": 0.9,
    "resting_hr_7d_avg": 46, "resting_hr_30d_avg": 50,
}


@pytest.fixture()
def user(db) -> User:
    from app.db.models import ProviderConnection

    u = User(email="a@x.it", display_name="Davide",
             password_hash="h")
    db.add(u)
    db.flush()
    db.add(ProviderConnection(user_id=u.id, provider="garmin",
                              external_id="a@x.it", secret_encrypted="cifrato"))
    db.commit()
    return u


def evaluate(db, user, snap, goal=None, **kwargs):
    return rules.evaluate(db, user, snap, compute_readiness(snap), goal, **kwargs)


# ============================================================================
# Definizione degli eventi
# ============================================================================

def test_event_types_are_unique_and_documented():
    keys = [e.key for e in rules.EVENT_TYPES]
    assert len(keys) == len(set(keys))
    for event in rules.EVENT_TYPES:
        assert event.label and event.description
        assert event.cooldown_days >= 1


def test_default_prefs_cover_every_event():
    prefs = rules.default_prefs()
    assert set(prefs) == {e.key for e in rules.EVENT_TYPES}


# ============================================================================
# Regole
# ============================================================================

def test_calm_state_produces_nothing(db, user):
    assert evaluate(db, user, CALM) == []


def test_overtraining_fires_above_threshold(db, user):
    found = evaluate(db, user, OVERLOADED)
    assert [n.event_type for n in found] == ["overtraining"]
    assert "1.8" in found[0].body


def test_overtraining_silent_just_below_threshold(db, user):
    snap = {**CALM, "load_ratio": rules.LOAD_RATIO_ALERT - 0.01}
    assert evaluate(db, user, snap) == []


def test_low_readiness_names_the_weak_factors(db, user):
    found = evaluate(db, user, EXHAUSTED, max_notifications=5)
    types = [n.event_type for n in found]
    assert "readiness_low" in types
    low = next(n for n in found if n.event_type == "readiness_low")
    assert "riposo" in low.body.lower()


def test_high_readiness_is_off_by_default(db, user):
    """L'utente non ha toccato le preferenze: gli avvisi positivi restano spenti."""
    assert [n.event_type for n in evaluate(db, user, PEAKING)] == []


def test_high_readiness_fires_when_enabled(db, user):
    user.notify_prefs_json = {"events": {"readiness_high": True}}
    db.commit()
    assert [n.event_type for n in evaluate(db, user, PEAKING)] == ["readiness_high"]


def test_sleep_decline(db, user):
    snap = {**CALM, "sleep_score_7d_avg": 55}
    found = evaluate(db, user, snap)
    assert found[0].event_type == "sleep_decline"
    assert found[0].url == "/sleep"


def test_sync_failure_comes_first(db, user):
    """Se i dati non arrivano, avvisare del carico sarebbe fuorviante."""
    user.sync_failures = 4
    db.commit()
    found = evaluate(db, user, OVERLOADED)
    assert found[0].event_type == "sync_failed"


def test_sync_failure_silent_below_threshold(db, user):
    user.sync_failures = rules.SYNC_FAILURES_ALERT - 1
    db.commit()
    assert "sync_failed" not in [n.event_type for n in evaluate(db, user, CALM)]


def test_race_countdown_in_the_final_week(db, user):
    from app import goals

    goal = goals.set_active_goal(
        db, user, "race_10k", target_date=date.today() + timedelta(days=4)
    )
    found = evaluate(db, user, CALM, goal)
    assert found[0].event_type == "race_countdown"
    assert "4 giorni" in found[0].title
    assert "scarico" in found[0].body


def test_race_countdown_on_race_day(db, user):
    from app import goals

    goal = goals.set_active_goal(db, user, "race_half", target_date=date.today())
    found = evaluate(db, user, CALM, goal)
    assert "Oggi" in found[0].title


def test_race_countdown_silent_when_far_away(db, user):
    from app import goals

    goal = goals.set_active_goal(
        db, user, "race_marathon", target_date=date.today() + timedelta(days=60)
    )
    assert evaluate(db, user, CALM, goal) == []


def test_only_one_notification_by_default(db, user):
    """Tre avvisi insieme la mattina è il modo più rapido per farsi disattivare."""
    user.sync_failures = 5
    db.commit()
    assert len(evaluate(db, user, EXHAUSTED)) == 1
    assert len(evaluate(db, user, EXHAUSTED, max_notifications=5)) > 1


def test_rules_are_ordered_by_urgency(db, user):
    found = evaluate(db, user, EXHAUSTED, max_notifications=5)
    types = [n.event_type for n in found]
    assert types.index("overtraining") < types.index("readiness_low")


# ============================================================================
# Deduplica
# ============================================================================

def test_recent_notification_is_not_repeated(db, user):
    db.add(NotificationLog(user_id=user.id, event_type="overtraining", channel="email",
                           title="t", body="b", ok=True))
    db.commit()
    assert evaluate(db, user, OVERLOADED) == []


def test_notification_repeats_after_the_cooldown(db, user):
    log = NotificationLog(user_id=user.id, event_type="overtraining", channel="email",
                          title="t", body="b", ok=True)
    db.add(log)
    db.commit()
    log.sent_at = datetime.utcnow() - timedelta(days=4)  # cooldown = 3 giorni
    db.commit()

    assert [n.event_type for n in evaluate(db, user, OVERLOADED)] == ["overtraining"]


def test_failed_delivery_does_not_block_a_retry(db, user):
    """Se la consegna è fallita, l'avviso deve poter ripartire."""
    db.add(NotificationLog(user_id=user.id, event_type="overtraining", channel="email",
                           title="t", body="b", ok=False))
    db.commit()
    assert [n.event_type for n in evaluate(db, user, OVERLOADED)] == ["overtraining"]


def test_dedup_is_per_user(db, user):
    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()

    db.add(NotificationLog(user_id=user.id, event_type="overtraining", channel="email",
                           title="t", body="b", ok=True))
    db.commit()

    assert evaluate(db, user, OVERLOADED) == []
    assert [n.event_type for n in evaluate(db, other, OVERLOADED)] == ["overtraining"]


def test_disabled_event_is_not_sent(db, user):
    user.notify_prefs_json = {"events": {"overtraining": False}}
    db.commit()
    assert evaluate(db, user, OVERLOADED) == []


# ============================================================================
# Riepilogo settimanale
# ============================================================================

def test_weekly_summary_only_on_sunday(db, user):
    user.notify_prefs_json = {"events": {"weekly_summary": True}}
    db.commit()

    monday = date(2026, 8, 10)
    sunday = date(2026, 8, 16)
    assert rules.weekly_summary(db, user, CALM, monday) is None
    assert rules.weekly_summary(db, user, CALM, sunday) is not None


def test_weekly_summary_is_off_by_default(db, user):
    assert rules.weekly_summary(db, user, CALM, date(2026, 8, 16)) is None


def test_weekly_summary_reports_the_numbers(db, user):
    user.notify_prefs_json = {"events": {"weekly_summary": True}}
    db.commit()
    snap = {**CALM, "consecutive_active_days": 5}
    summary = rules.weekly_summary(db, user, snap, date(2026, 8, 16))
    assert "5 giorni attivi" in summary.body
    assert "sonno medio 80" in summary.body


# ============================================================================
# Canali
# ============================================================================

def test_unconfigured_channels_are_unavailable(db, user, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "")
    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "")

    for channel in CHANNELS.values():
        assert channel.is_configured is False
        assert channel.is_available_for(db, user) is False


def test_email_channel_needs_host_and_sender(db, user, monkeypatch):
    channel = EmailChannel()
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtp.example.it")
    monkeypatch.setattr("app.config.settings.SMTP_FROM", "")
    assert channel.is_configured is False

    monkeypatch.setattr("app.config.settings.SMTP_FROM", "coach@example.it")
    assert channel.is_configured is True
    assert channel.is_available_for(db, user) is True


def test_telegram_needs_the_user_to_have_linked_it(db, user, monkeypatch):
    channel = TelegramChannel()
    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "123:abc")
    assert channel.is_available_for(db, user) is False  # chat_id assente

    user.telegram_chat_id = "999"
    db.commit()
    assert channel.is_available_for(db, user) is True


def test_webpush_needs_a_subscription(db, user, monkeypatch):
    channel = WebPushChannel()
    monkeypatch.setattr("app.config.settings.VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setattr("app.config.settings.VAPID_PRIVATE_KEY", "priv")
    assert channel.is_available_for(db, user) is False

    db.add(PushSubscription(user_id=user.id, endpoint="https://push.example/1",
                            p256dh="k", auth="a"))
    db.commit()
    assert channel.is_available_for(db, user) is True


def test_email_send_reports_failure_without_raising(db, user, monkeypatch):
    monkeypatch.setattr("app.config.settings.SMTP_HOST", "smtp.invalido.local")
    monkeypatch.setattr("app.config.settings.SMTP_FROM", "coach@example.it")

    def boom(*args, **kwargs):
        raise OSError("host irraggiungibile")

    monkeypatch.setattr("smtplib.SMTP", boom)
    assert EmailChannel().send(db, user, Notification("x", "t", "b")) is False


def test_telegram_send_reports_failure_without_raising(db, user, monkeypatch):
    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "123:abc")
    user.telegram_chat_id = "999"
    db.commit()

    def boom(*args, **kwargs):
        raise OSError("rete assente")

    monkeypatch.setattr("httpx.post", boom)
    assert TelegramChannel().send(db, user, Notification("x", "t", "b")) is False


# ============================================================================
# Dispatcher
# ============================================================================

class FakeChannel:
    key = "finto"
    label = "Finto"
    is_configured = True

    def __init__(self, works: bool = True):
        self.works = works
        self.sent: list[Notification] = []

    def is_available_for(self, db, user):
        return True

    def send(self, db, user, notification):
        self.sent.append(notification)
        return self.works


@pytest.fixture()
def fake_channel(monkeypatch):
    channel = FakeChannel()
    monkeypatch.setattr("app.notifications.dispatcher.CHANNELS", {"finto": channel})
    return channel


def test_dispatch_delivers_and_logs(db, user, fake_channel):
    sent = dispatch_for_user(db, user, OVERLOADED, compute_readiness(OVERLOADED))

    assert sent == 1
    assert fake_channel.sent[0].event_type == "overtraining"
    log = db.query(NotificationLog).one()
    assert log.ok is True and log.channel == "finto"


def test_dispatch_sends_nothing_when_calm(db, user, fake_channel):
    assert dispatch_for_user(db, user, CALM, compute_readiness(CALM)) == 0
    assert fake_channel.sent == []


def test_failed_delivery_is_logged_as_failure(db, user, monkeypatch):
    channel = FakeChannel(works=False)
    monkeypatch.setattr("app.notifications.dispatcher.CHANNELS", {"finto": channel})

    assert dispatch_for_user(db, user, OVERLOADED, compute_readiness(OVERLOADED)) == 0
    assert db.query(NotificationLog).one().ok is False


def test_a_broken_channel_does_not_stop_the_others(db, user, monkeypatch):
    class ExplodingChannel(FakeChannel):
        def send(self, db, user, notification):
            raise RuntimeError("il canale è esploso")

    working = FakeChannel()
    monkeypatch.setattr(
        "app.notifications.dispatcher.CHANNELS",
        {"rotto": ExplodingChannel(), "buono": working},
    )

    assert dispatch_for_user(db, user, OVERLOADED, compute_readiness(OVERLOADED)) == 1
    assert len(working.sent) == 1
    assert {log.ok for log in db.query(NotificationLog).all()} == {True, False}


def test_channel_preferences_are_respected(db, user, monkeypatch):
    a, b = FakeChannel(), FakeChannel()
    a.key, b.key = "a", "b"
    monkeypatch.setattr("app.notifications.dispatcher.CHANNELS", {"a": a, "b": b})

    user.notify_prefs_json = {"channels": {"a": True, "b": False}}
    db.commit()

    dispatch_for_user(db, user, OVERLOADED, compute_readiness(OVERLOADED))
    assert len(a.sent) == 1 and b.sent == []


def test_without_preferences_all_channels_are_used(db, user, monkeypatch):
    a, b = FakeChannel(), FakeChannel()
    a.key, b.key = "a", "b"
    monkeypatch.setattr("app.notifications.dispatcher.CHANNELS", {"a": a, "b": b})

    dispatch_for_user(db, user, OVERLOADED, compute_readiness(OVERLOADED))
    assert len(a.sent) == 1 and len(b.sent) == 1


def test_no_channel_means_nothing_is_logged(db, user, monkeypatch):
    monkeypatch.setattr("app.notifications.dispatcher.CHANNELS", {})
    assert dispatch_for_user(db, user, OVERLOADED, compute_readiness(OVERLOADED)) == 0
    assert db.query(NotificationLog).count() == 0


def _seed_an_overloaded_month(db, user) -> None:
    """Tre settimane tranquille e poi una settimana durissima.

    Il carico si ricava dalle attività, non da `training_load`: Garmin quel
    campo non lo popola per tutti gli account, ed era il motivo per cui questa
    regola in produzione non poteva scattare mai.
    """
    from app.db.models import Activity, DailyWellness, SleepRecord

    today = date.today()
    for i in range(30):
        day = today - timedelta(days=i)
        db.add(DailyWellness(user_id=user.id, day=day, resting_hr=55))
        db.add(SleepRecord(user_id=user.id, day=day, sleep_score=75))

    # Settimana in corso: un'ora tutti i giorni, a ritmo impegnativo.
    for i in range(7):
        db.add(Activity(
            user_id=user.id, external_id=5000 + i, activity_type="running",
            start_time=datetime.combine(today - timedelta(days=i), datetime.min.time()),
            duration_sec=3600, avg_hr=160, max_hr=180, distance_m=12000,
        ))
    # Le tre settimane prima: mezz'ora blanda ogni tre giorni.
    for i in range(7, 28, 3):
        db.add(Activity(
            user_id=user.id, external_id=6000 + i, activity_type="running",
            start_time=datetime.combine(today - timedelta(days=i), datetime.min.time()),
            duration_sec=1800, avg_hr=125, max_hr=150, distance_m=5000,
        ))
    db.commit()


def test_dispatch_computes_the_snapshot_itself(db, user, fake_channel):
    """Chiamato senza snapshot, se lo calcola dal database."""
    _seed_an_overloaded_month(db, user)

    assert dispatch_for_user(db, user) == 1
    assert fake_channel.sent[0].event_type == "overtraining"


# ============================================================================
# Integrazione con la pipeline
# ============================================================================

def test_pipeline_dispatches_notifications(db, user, monkeypatch, fake_channel):
    from app import pipeline

    _seed_an_overloaded_month(db, user)

    monkeypatch.setattr("app.providers.sync_user", lambda d, u: {"activities": 0})
    result = pipeline.run_for_user(db, user)

    assert result.notifications == 1
    assert fake_channel.sent[0].event_type == "overtraining"


def test_notification_failure_does_not_break_the_pipeline(db, user, monkeypatch):
    from app import pipeline

    def boom(*args, **kwargs):
        raise RuntimeError("dispatcher rotto")

    monkeypatch.setattr("app.notifications.dispatcher.dispatch_for_user", boom)
    monkeypatch.setattr("app.providers.sync_user", lambda d, u: {"activities": 3})

    result = pipeline.run_for_user(db, user)
    assert result.synced == {"activities": 3}  # la sync è comunque andata
    assert any("notifiche" in e for e in result.errors)
