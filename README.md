# Garmin Connector

Dashboard personale per i dati di **Garmin Connect** + integrazione AI (predisposta) per piani di allenamento.

📄 Documentazione completa: **[CLAUDE.md](./CLAUDE.md)**

## Avvio rapido (locale)

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # inserisci GARMIN_EMAIL e GARMIN_PASSWORD
uvicorn app.main:app --reload
```

Apri http://localhost:8000 e premi **Sincronizza**.

## Docker

```bash
cp .env.example .env
docker compose up --build
```

## Stack

FastAPI · SQLAlchemy (SQLite → Postgres) · python-garminconnect · Jinja2 · Docker
