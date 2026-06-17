# Garmin Connector

Dashboard personale per monitorare i dati di **Garmin Connect** e fornirli a un modello AI
per generare piani di allenamento.

## Cos'è stato fatto

- **Connessione Garmin**: tramite la libreria [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect) (cyberjunky). Login email/password (no MFA) con **cache del token** su disco: non si rifà il login a ogni avvio.
- **Backend**: FastAPI che serve una **web app multi-pagina** (Jinja2 + Chart.js) e le API JSON.
- **Database**: SQLite via **SQLAlchemy** (ORM). Per il deploy su VPS Hetzner basta cambiare `DATABASE_URL` nel `.env` (es. Postgres) senza toccare il codice.
- **Modulo AI**: interfaccia provider-agnostica già predisposta. Attivo uno **stub** che prepara il payload strutturato; l'integrazione vera (OpenAI/Claude) si aggiunge in `app/ai/provider.py`.
- **Docker**: `Dockerfile` + `docker-compose.yml` pronti per il deploy.

## Pagine (web app)

| Pagina | URL | Contenuto |
|--------|-----|-----------|
| Panoramica | `/` | KPI del giorno + mini-trend (passi, FC riposo, sleep score, VO₂max) |
| Attività | `/activities` | Elenco allenamenti + distribuzione per tipo; dettaglio su `/activities/{id}` |
| Sonno | `/sleep` | Fasi del sonno (grafico impilato), sleep score, tabella |
| Salute | `/health` | FC, stress, Body Battery, SpO₂, respirazione, passi, intensità |
| Corpo | `/body` | Peso, BMI, massa grassa/muscolare (richiede bilancia Garmin Index) |
| Performance | `/performance` | VO₂max, load, HRV, readiness + record personali e race predictor (live) |
| Dispositivi | `/devices` | Device, gear/attrezzatura, badge (live) |
| AI Insights | `/ai` | Generazione piani/insight + guida all'integrazione LLM |

## Dati salvati nel DB (time-series per i grafici)

| Tabella | Campi principali |
|---------|------------------|
| `activities` | tipo, data, distanza, durata, FC, passo, dislivello, cadenza, potenza, training effect |
| `sleep_records` | durata totale, fasi (profondo/leggero/REM), score, FC riposo, SpO₂, respirazione |
| `training_metrics` | VO₂max (corsa/bici), training status, load, HRV, readiness |
| `daily_wellness` | passi, FC riposo/min/max, stress, Body Battery, SpO₂, respirazione, intensità, calorie |
| `body_composition` | peso, BMI, massa grassa/acqua/muscolare/ossea |

I dati "snapshot" (dispositivi, gear, record personali, race predictor, dettaglio singola attività)
sono letti **live** da `app/garmin/service.py` con cache in memoria (TTL 5 min).

## Struttura

```
app/
├── main.py            # FastAPI: include router + endpoint sync/api/ai
├── config.py          # carica .env
├── templating.py      # Jinja2 + filtri di formattazione condivisi
├── queries.py         # query DB → serie storiche per grafici/tabelle
├── routers/
│   └── pages.py       # tutte le pagine HTML
├── garmin/
│   ├── client.py      # login Garmin + cache token (singleton)
│   ├── sync.py        # scarica e salva i time-series (parsing difensivo)
│   └── service.py     # letture live snapshot (devices, gear, record, dettaglio attività)
├── db/
│   ├── database.py    # engine/session SQLAlchemy
│   └── models.py      # Activity, SleepRecord, TrainingMetric, DailyWellness, BodyComposition
├── ai/
│   ├── base.py        # interfaccia astratta AIProvider
│   ├── provider.py    # StubProvider (attivo) + OpenAIProvider (scheletro)
│   └── context.py     # costruisce il contesto dati per l'AI
├── templates/         # base.html + una pagina per sezione
└── static/{style.css, app.js}   # app.js include gli helper Chart.js
```

## Endpoint API

| Metodo | Path | Descrizione |
|--------|------|-------------|
| POST | `/sync` | Scarica i dati freschi da Garmin nel DB |
| GET | `/api/context` | Contesto dati strutturato completo (JSON) — input per l'AI |
| POST | `/ai/plan` | Genera un piano/insight (provider da `AI_PROVIDER`) |
| GET | `/healthz` | Healthcheck |

## Setup locale

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # poi inserisci email/password Garmin
uvicorn app.main:app --reload
# apri http://localhost:8000
```

Primo utilizzo: apri la dashboard e premi **Sincronizza** per scaricare i dati.

## Docker

```bash
cp .env.example .env        # compila le credenziali
docker compose up --build
```

## Prossimi passi

- Collegare il provider AI reale (OpenAI o Claude) in `app/ai/provider.py` → metodo `generate_training_plan`.
- Sync automatica schedulata (APScheduler o cron).
- Migrazione a Postgres per il deploy su Hetzner (servizio già predisposto in `docker-compose.yml`).
- Migrazioni di schema con Alembic (oggi: `create_all`; se cambi i modelli, vai di Alembic o rigenera il DB).

## Note tecniche

- Richiede **Python 3.11+** (la libreria garminconnect non supporta 3.9). In locale si usa un venv con 3.11.
- Avvia **sempre** `uvicorn` dal venv (`./venv/bin/uvicorn ...` oppure dopo `source venv/bin/activate`), altrimenti l'uvicorn globale non trova le dipendenze.
- Segreti in `.env` (gitignored). Mai committare credenziali o la cartella `data/`.
- Il parsing delle risposte Garmin è difensivo: i campi mancanti diventano `None`/`—`, la sync e le pagine non si interrompono.
- Lo schema oggi si crea con `Base.metadata.create_all`: **aggiungendo colonne ai modelli**, su SQLite va rigenerato il DB (`rm data/garmin_connector.db`) o introdotto Alembic.
