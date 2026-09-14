"""Fixture condivise.

L'app di test gira su un SQLite temporaneo e con un Garmin finto: nessun test
tocca mai la rete o il database reale.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def no_background_scheduler(monkeypatch):
    """Lo scheduler reale non deve mai partire durante i test."""
    monkeypatch.setattr("app.config.settings.SCHEDULER_ENABLED", False)


@pytest.fixture(autouse=True)
def no_real_ai_calls(monkeypatch):
    """Nessun test chiama davvero un modello.

    `get_provider()` legge `AI_PROVIDER` dalle impostazioni, che in sviluppo
    arrivano dal `.env` reale: senza questo blocco la suite chiamava l'API di
    Anthropic a ogni `refresh_daily_cache`. Costava soldi veri, rendeva i test
    dipendenti dalla rete, e faceva fallire in modo intermittente quelli che
    si aspettano il coaching deterministico.

    Le chiavi vengono azzerate oltre al provider: così anche un test che
    reimposta `AI_PROVIDER` per conto suo non riesce comunque ad autenticarsi.
    """
    monkeypatch.setattr("app.config.settings.AI_PROVIDER", "stub")
    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("app.config.settings.OPENAI_API_KEY", "")


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Ogni test parte con i contatori del limite di frequenza azzerati.

    L'app è costruita una volta per l'intera sessione, quindi il middleware
    conterebbe le richieste di tutti i test insieme: la fixture che registra
    un utente da sola basterebbe a esaurire il tetto di `/register` dopo
    cinque test. Chi vuole *provare* il limite lo esaurisce dentro il proprio
    test, e ricomincia da zero al successivo.
    """
    from app.security import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture()
def test_db(monkeypatch, tmp_path):
    """Engine + sessionmaker su un DB temporaneo, con le tabelle già create."""
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.settings.SESSION_SECRET", "test-secret")
    monkeypatch.setattr("app.config.settings.GARMIN_TOKENSTORE", str(tmp_path / "tokens"))

    from app.db.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    import app.db.models  # noqa: F401 — registra i modelli su Base

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Il codice che apre una sessione per conto suo (scheduler, streaming SSE)
    # non passa dalla dependency di FastAPI. Senza questo patch scriverebbe sul
    # database reale durante i test.
    monkeypatch.setattr("app.db.database.SessionLocal", factory)
    monkeypatch.setattr("app.routers.chat.SessionLocal", factory)
    monkeypatch.setattr("app.scheduler.SessionLocal", factory)

    # E l'**engine**, non solo le sessioni. `init_db()` fa `create_all` su
    # `app.db.database.engine`, che non passa da `SessionLocal`: reindirizzare
    # solo quest'ultimo lasciava scoperta la CLI, che chiama `init_db()` in
    # quasi tutti i comandi. Il risultato l'ho trovato addosso: la suite ha
    # creato `daily_checkins` nel database di sviluppo vero, e la migrazione
    # successiva è morta su «table already exists» — cioè esattamente lo stato
    # ibrido contro cui il CLAUDE.md mette in guardia per `--reload`.
    monkeypatch.setattr("app.db.database.engine", engine)

    return factory


@pytest.fixture()
def db(test_db):
    """Sessione diretta sul DB di test (per i test che non passano dall'HTTP)."""
    session = test_db()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(test_db, monkeypatch):
    """TestClient con la dependency del DB sostituita e Garmin finto.

    Le credenziali Garmin sono accettate a meno che la password sia "wrong":
    così i test possono simulare sia il collegamento riuscito sia quello
    rifiutato. Dalla v3 la verifica non sta più nel login — l'accesso all'app
    non contatta nessun fornitore — ma nel collegamento della sorgente.
    """
    monkeypatch.setattr(
        "app.garmin.client.validate_credentials", lambda email, password: password != "wrong"
    )

    from app.db.database import get_session
    from app.main import app

    def override_get_session():
        session = test_db()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


TEST_EMAIL = "test@x.it"
TEST_PASSWORD = "password-di-prova"


@pytest.fixture()
def registered_client(client, test_db):
    """Utente registrato e autenticato, ma **senza** sorgente dati collegata.

    È lo stato reale fra la registrazione e il primo collegamento, e adesso che
    esiste va poterlo provare: il menu è ridotto, le pagine di salute
    rimandano al coach, la sync non fa niente e non conta come errore.
    """
    from app.db.models import InviteCode

    session = test_db()
    session.add(InviteCode(code="TEST-INVITE"))
    session.commit()
    session.close()

    response = client.post("/register", data={
        "email": TEST_EMAIL, "password": TEST_PASSWORD,
        "password_confirm": TEST_PASSWORD, "display_name": "Test",
        "invite_code": "TEST-INVITE",
    })
    assert response.status_code == 303, response.text
    return client


@pytest.fixture()
def logged_client(registered_client, test_db):
    """Utente registrato **con Garmin collegato**: il caso pieno di capacità.

    Quasi tutti i test danno per scontato di vedere sonno, recupero e corpo,
    che è appunto quello che si ottiene con Garmin. Chi vuole provare il caso
    Strava — capacità ridotte — usa `strava_client`.
    """
    _attach(test_db, "garmin")
    return registered_client


@pytest.fixture()
def strava_client(registered_client, test_db):
    """Utente con Strava: solo attività e zone, niente sonno né recupero."""
    _attach(test_db, "strava")
    return registered_client


def _attach(test_db, provider: str) -> None:
    """Collega una sorgente scrivendo direttamente sul DB, senza rete."""
    from sqlalchemy import select

    from app.db.models import ProviderConnection, User

    session = test_db()
    try:
        user = session.scalar(select(User).where(User.email == TEST_EMAIL))
        session.add(ProviderConnection(
            user_id=user.id,
            provider=provider,
            external_id=TEST_EMAIL if provider == "garmin" else "12345",
            secret_encrypted="cifrato",
            status="ok",
        ))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def logged_user(logged_client, test_db):
    """L'utente corrispondente a `logged_client`."""
    from sqlalchemy import select

    from app.db.models import User

    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == TEST_EMAIL))
    finally:
        session.close()
