# Garmin Coach

Coach personale sui dati di **Garmin Connect**: dashboard interpretate, chat con
un allenatore AI che conosce i tuoi dati, obiettivi configurabili, piani di
allenamento e notifiche.

**Stato: completo e in produzione** su https://garmin.46.224.17.241.sslip.io
440 test verdi.

## Documenti di riferimento

| Documento | Cosa contiene |
|---|---|
| `docs/ARCHITETTURA.md` | **Catalogo sistematico dei componenti**: cosa fa ogni modulo |
| `docs/superpowers/specs/2026-08-14-garmin-coach-v2-multiuser-ai-design.md` | Le decisioni architetturali e il perché |
| `PRD_GarminCoach.md` | Specifiche di interfaccia e pagine |
| `AI_COACHING_DESIGN.md` | Motori readiness/insight, gamification, piani |

## Il principio che regge tutto

**Il determinismo fa i numeri, l'AI fa le parole.**

Readiness, letture delle dashboard, trend, adattamento dei piani, XP e regole di
notifica sono Python puro: gratis, istantanei, testabili. L'AI riceve solo
sintesi già calcolate e produce linguaggio o pianificazione.

Da qui discende il controllo dei costi (budget ~5 €/mese):
- il system prompt della chat contiene solo lo stato di oggi, poche centinaia di token;
- per i dati storici il modello chiama i tool in `app/ai/tools.py`, che aggregano
  da soli (oltre 35 giorni → medie settimanali, oltre 180 → mensili);
- il coaching giornaliero è cachato: una chiamata al giorno per utente, non una per pageview;
- quote per utente, con consumo e spesa stimata visibili in `/admin`.

Prima di aggiungere una feature, chiediti se il calcolo può stare in Python.
Se sì, ci sta.

## Accesso

Le credenziali Garmin **sono** il login: bcrypt per la verifica locale (veloce,
non contatta Garmin), copia cifrata con Fernet perché la sync notturna deve poter
rifare il login quando il token scade.

Registrazione **a invito**:
```bash
docker exec garmin-app python -m app.cli create-invite   # oppure dalla pagina /admin
```

## Pagine

| Pagina | URL | Contenuto |
|--------|-----|-----------|
| Coach | `/coach` | Prontezza, allenamento di oggi, insight, obiettivo, progressi |
| Coach AI | `/chat` | Chat con tool sui dati, risposta in streaming |
| Panoramica | `/` | KPI e grafici |
| Attività | `/activities` | Storico, distribuzione delle intensità, dettaglio |
| Obiettivo | `/goals` | Il "laboratorio": tipo, parametri, data target, note |
| Piano | `/plan` | Piano generato, settimana corrente, adattamento giornaliero |
| Sonno · Salute · Corpo · Performance | `/sleep` `/health` `/body` `/performance` | Grafici **con la lettura**: verdetto, evidenza, cosa fare |
| Impostazioni | `/settings` | Notifiche per evento e per canale, account |
| Amministrazione | `/admin` | Utenti, inviti, quote, spesa AI (solo admin) |

## Dati

Time-series salvate nel DB, tutte con `user_id` e unicità composita `(user_id, giorno)`:
`activities`, `sleep_records`, `training_metrics`, `daily_wellness`, `body_composition`.

Tabelle applicative: `users`, `invite_codes`, `user_goals`, `training_plans`,
`daily_coach_cache`, `gamification_state`, `chat_conversations`, `chat_messages`,
`ai_usage_log`, `notification_log`, `push_subscriptions`.

I dati snapshot (dispositivi, gear, record personali) sono letti **live** da
`app/garmin/service.py`, con cache in memoria keyed per utente (TTL 5 min).

## Comandi

```bash
# Sviluppo
./venv/bin/uvicorn app.main:app --reload
./venv/bin/pytest -q

# Amministrazione
python -m app.cli create-invite [--expires-days 30]
python -m app.cli list-users
python -m app.cli bootstrap-from-env   # crea il primo utente dalle credenziali nel .env
python -m app.cli vapid-keys           # chiavi per le notifiche push
python -m app.cli telegram-webhook     # URL da registrare presso Telegram

# Deploy
rsync -az --delete --exclude '.git' --exclude venv --exclude data --exclude '.env*' \
  ./ deploy@46.224.17.241:/home/deploy/garmin-connector/
ssh deploy@46.224.17.241 'cd /home/deploy/garmin-connector && \
  docker compose --env-file .env.prod -p garmin -f docker-compose.prod.yml up -d --build'
```

`--env-file .env.prod` è obbligatorio: Compose legge `env_file` solo per le
variabili *dentro* il container, non per interpolare il compose stesso.

## Se cambi i modelli

Le migrazioni sono con Alembic e girano da sole all'avvio del container.

```bash
./venv/bin/alembic revision --autogenerate -m "cosa cambia"
./venv/bin/pytest tests/test_migrations.py   # schema e modelli devono coincidere
```

**SQLite e Postgres non si comportano allo stesso modo.** Due bug sono già
emersi solo in produzione:

- `Integer` su Postgres sta in 4 byte: per gli id di Garmin serve `BigInteger`
  (presidiato da `test_activity_id_column_is_64_bit`);
- dopo un errore Postgres invalida l'intera transazione, quindi serve
  `rollback()` prima di scrivere qualcos'altro nel gestore dell'eccezione.

## Note tecniche

- Richiede **Python 3.11+**. Avvia sempre dal venv (`./venv/bin/...`).
- Segreti in `.env` (gitignored) e `.env.prod` sul server. Mai in git.
- Il parsing delle risposte Garmin è difensivo: i campi mancanti diventano
  `None`/`—`, la sync e le pagine non si interrompono.
- Ogni passo della pipeline è isolato: se la sync fallisce, coaching,
  gamification e notifiche proseguono comunque.
- I test non toccano mai la rete né il database reale: provider finti e
  `SessionLocal` reindirizzato (presidiato da
  `test_session_factory_points_at_the_test_database`).
- L'app funziona senza chiave AI: chat e coaching narrativo si disattivano da
  soli, tutto il resto resta deterministico.
