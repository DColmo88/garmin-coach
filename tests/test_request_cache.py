"""Il quadro del carico si calcola una volta per richiesta, non quattro.

`summary.build` legge centottanta giorni di attività, risolve il profilo
fisiologico — che a sua volta cerca il picco di FC su un anno — e srotola una
serie PMC di centottanta punti. Su una singola apertura di `/coach` girava
quattro volte, perché quattro componenti diversi lo chiedevano senza sapere
l'uno dell'altro.
"""
from __future__ import annotations

import pytest

from app.analysis import cache as analysis_cache


@pytest.fixture()
def counting_summary(monkeypatch):
    """Conta quante volte si calcola davvero il carico."""
    from app.analysis import summary

    calls = []
    original = summary.build

    def counted(db, user, today=None, **kwargs):
        calls.append(today)
        return original(db, user, today, **kwargs)

    monkeypatch.setattr(summary, "build", counted)
    return calls


def test_outside_a_request_nothing_is_cached(db, counting_summary):
    """Scheduler, CLI e test calcolano ogni volta: nessuno azzera la memoria."""
    from app.db.models import User

    user = User(email="a@x.it", password_hash="h")
    db.add(user)
    db.commit()

    analysis_cache.end_request()
    analysis_cache.load_summary(db, user)
    analysis_cache.load_summary(db, user)

    assert len(counting_summary) == 2


def test_inside_a_request_it_is_computed_once(db, counting_summary):
    from app.db.models import User

    user = User(email="a@x.it", password_hash="h")
    db.add(user)
    db.commit()

    analysis_cache.begin_request()
    try:
        analysis_cache.load_summary(db, user)
        analysis_cache.load_summary(db, user)
        analysis_cache.load_summary(db, user)
    finally:
        analysis_cache.end_request()

    assert len(counting_summary) == 1


def test_two_users_do_not_share_the_answer(db, counting_summary):
    from app.db.models import User

    alice = User(email="alice@x.it", password_hash="h")
    bob = User(email="bob@x.it", password_hash="h")
    db.add_all([alice, bob])
    db.commit()

    analysis_cache.begin_request()
    try:
        analysis_cache.load_summary(db, alice)
        analysis_cache.load_summary(db, bob)
    finally:
        analysis_cache.end_request()

    assert len(counting_summary) == 2


def test_a_real_page_computes_it_once(logged_client, counting_summary):
    """La verifica che conta: la pagina più visitata dell'app.

    Serve a presidiare anche il fatto che il `ContextVar` acceso dal middleware
    arrivi fino all'handler — Starlette esegue il resto della catena in un task
    suo, e se il contesto non si propagasse la cache resterebbe spenta senza
    che niente lo segnali.
    """
    counting_summary.clear()
    logged_client.get("/coach")

    assert len(counting_summary) == 1


def test_the_cache_does_not_survive_the_request(logged_client, counting_summary):
    counting_summary.clear()
    logged_client.get("/coach")
    logged_client.get("/coach")

    assert len(counting_summary) == 2
