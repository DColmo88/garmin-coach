# Architettura — Garmin Coach v2

**Catalogo sistematico dei componenti.** Questo è il documento di riferimento su *cosa fa ogni pezzo dell'app*.
Per il *perché* delle scelte vedi la spec `superpowers/specs/2026-08-14-garmin-coach-v2-multiuser-ai-design.md`.
Per il *cosa si vede a schermo* vedi `../PRD_GarminCoach.md`.

> **Stato al 14 agosto 2026: completo e in produzione.**
> 440 test verdi · https://garmin.46.224.17.241.sslip.io

---

## 0. Mappa a colpo d'occhio

```
                            ┌──────────────────────────────┐
        Browser (PWA) ─────►│  Caddy  (TLS, reverse proxy) │
                            └──────────────┬───────────────┘
                                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      garmin-app  ·  FastAPI (un processo)                │
│                                                                          │
│  PRESENTAZIONE      routers/       templates/       static/              │
│      pages · auth · chat · api          Jinja2            CSS · JS       │
│  ───────────────────────────────────────────────────────────────────────│
│  DOMINIO            auth/      ai/       gamification/    notifications/ │
│                   sessioni  readiness      XP/badge         dispatcher   │
│                   crypto    insights                        3 canali     │
│                   login     chat+tools                                   │
│                             coaching                                     │
│                             training_plan                                │
│  ───────────────────────────────────────────────────────────────────────│
│  DATI               db/models · db/database · queries · garmin/sync      │
│  INTEGRAZIONI       garmin/client · garmin/service · ai/provider         │
│  AUTOMAZIONE        scheduler (APScheduler in-process)                   │
└───────────┬──────────────────────┬────────────────────┬─────────────────┘
            ▼                      ▼                    ▼
      Postgres (prod)        Garmin Connect       Claude / OpenAI
      SQLite (dev)           (per-utente)         Brevo · Telegram · WebPush
```

**Regola d'oro del sistema:** *il determinismo fa i numeri, l'AI fa le parole.*
Ogni punteggio, trend, soglia e conteggio è calcolato in Python puro (testabile, gratuito, istantaneo).
L'AI riceve solo sintesi già calcolate e produce linguaggio o pianificazione.

---

## 1. Presentazione

### 1.1 `app/routers/pages.py`
Tutte le pagine HTML. Ogni route: risolve l'utente dalla sessione, chiama `queries.py`, passa il risultato al template.
Non contiene logica di dominio — solo orchestrazione e formattazione.

| Route | Pagina |
|---|---|
| `/coach` | Landing: readiness, allenamento suggerito, insight, progresso |
| `/` | Panoramica: KPI + grafici + attività recenti |
| `/activities`, `/activities/{id}` | Storico e dettaglio allenamenti |
| `/sleep`, `/health`, `/performance`, `/body` | Dashboard tematiche |
| `/goals` | Il "laboratorio": obiettivo attivo e parametri |
| `/plan` | Piano di allenamento generato |
| `/settings` | Preferenze notifiche, account |
| `/admin` | Utenti, inviti, consumo AI (solo `is_admin`) |

### 1.2 `app/routers/auth.py`
`GET/POST /login` (credenziali Garmin + codice invito al primo accesso), `POST /logout`.
Traduce le eccezioni di `auth/service.py` in messaggi d'errore leggibili nel form.

### 1.3 `app/routers/chat.py`
`GET /chat` (pagina) e `POST /chat/message` con risposta **streaming SSE**.
Delega tutto ad `ai/chat.py`; il router gestisce solo il protocollo di streaming e la quota.

### 1.4 `app/templates/`
`base.html` (sidebar, topbar, stato sync) + una pagina per sezione + `_components.html` (macro riusabili: KPI card, insight card, ring SVG, session card).
`login.html` è standalone (niente sidebar).

### 1.5 `app/static/`
`style.css`: design system dark (token colore/spaziatura/tipografia, componenti).
`app.js`: helper Chart.js condivisi, toast di sync, client SSE della chat.
`manifest.json` + `sw.js` per la PWA (installabilità + ricezione push).

### 1.6 `app/templating.py`
Istanza Jinja2 condivisa + filtri di formattazione (durate, distanze, date, valori mancanti → `—`).

---

## 2. Dominio

### 2.1 `app/auth/` — identità e accesso
| File | Responsabilità |
|---|---|
| `security.py` | `encrypt_secret`/`decrypt_secret` (Fernet, per la password usata dalla sync) · `hash_password`/`verify_password` (bcrypt, per il login) |
| `session.py` | Cookie firmato (itsdangerous, 30gg) · dependency `require_user`/`optional_user` · eccezione `LoginRequired` |
| `service.py` | `authenticate()`: login utente esistente, registrazione a invito, self-healing se la password Garmin cambia |

**Modello di sicurezza:** la password Garmin è il login. Verifica locale con bcrypt (veloce, non contatta Garmin); copia cifrata Fernet perché la sync notturna deve poter rifare il login quando il token scade. `FERNET_KEY` e `SESSION_SECRET` vivono solo nel `.env` del server.

### 2.2 `app/ai/` — intelligenza
| File | Tipo | Responsabilità |
|---|---|---|
| `readiness.py` | deterministico | Score 0-100 da sonno/HRV/battery/carico/FC riposo, con breakdown per fattore e banda (Pronto→Riposo) |
| `insights.py` | deterministico | ~8 regole su trend (FC riposo, carico, sonno, VO₂max, streak…) → `Insight(icona, titolo, testo, colore)`, ordinati per priorità |
| `context.py` | deterministico | Costruisce il payload dati strutturato per i prompt |
| `base.py` | interfaccia | `AIProvider` astratto |
| `provider.py` | integrazione | `ClaudeProvider` + `OpenAIProvider` + `StubProvider`, scelti da `AI_PROVIDER`; modello "light" (chat/coaching) e "heavy" (piani) |
| `coaching.py` | ibrido | Messaggio del coach giornaliero: deterministico come fallback, riscritto dall'AI una volta al giorno |
| `tools.py` | deterministico | I tool che la chat può chiamare: query DB **già aggregate** (mai serie raw lunghe) |
| `chat.py` | AI | Loop agentico: system prompt (profilo + obiettivo + snapshot) → tool-calling → risposta |
| `../training.py` | AI + deterministico | Generazione piano (modello heavy, output JSON) + risoluzione della sessione di oggi con adattamento da readiness |

**Come si controlla il costo (il punto che mi hai chiesto esplicitamente):**
1. Il system prompt contiene solo una **sintesi compatta** pre-calcolata: readiness di oggi, trend 7/28gg, ultimo allenamento, obiettivo, settimana di piano. Poche centinaia di token.
2. Se la conversazione tocca periodi storici o dettagli, il modello **chiama un tool** e riceve solo quel dato, già aggregato (granularità settimanale/mensile per periodi lunghi).
3. Nessun dato grezzo viene mai spedito "a pioggia".
4. Quote per utente + cache giornaliera del coaching + `AIUsageLog` per vedere quanto si spende.

### 2.3 `app/gamification.py`
Calcola XP, serie e traguardi dai dati già nel DB, dopo ogni sync. Nessuno stato
da mantenere coerente: se arrivano dati in ritardo, i conteggi si sistemano da soli.
Nessun input utente, nessuna AI.

### 2.4 `app/insights/` — l'interpretazione delle dashboard
| File | Responsabilità |
|---|---|
| `stats.py` | Statistica tollerante ai buchi: medie, pendenze, dispersione. Restituisce `None` invece di inventare un trend su due punti |
| `domains.py` | Una `read_*` per pagina che produce **verdetto, evidenza, azione** più le note dei singoli grafici |

È il livello che risponde alla richiesta "non voglio dati crudi buttati lì".
Incrocia più segnali invece di guardarne uno solo: quantità × qualità × regolarità
per il sonno, FC a riposo × ricarica notturna per la salute, crescita del VO₂max
contro il rapporto di carico per la performance, e la distribuzione delle intensità
per gli allenamenti.

### 2.5 `app/notifications/`
| File | Responsabilità |
|---|---|
| `rules.py` | Regole deterministiche che decidono *se* notificare (readiness critico, overtraining, sessione chiave, gara vicina, sync fallita, riepilogo settimanale) + dedup |
| `dispatcher.py` | Prende un evento, legge le preferenze utente, instrada ai canali attivi |
| `channels.py` | I tre canali (Web Push, email SMTP, Telegram) dietro la stessa interfaccia: `send(user, notification)` |

L'utente sceglie in `/settings` quale tipo di evento su quale canale. Default conservativo: solo i warning importanti.

---

## 3. Dati

### 3.1 `app/db/models.py`
**Tabelle time-series** (una riga per giorno/attività, tutte con `user_id`): `activities`, `sleep_records`, `training_metrics`, `daily_wellness`, `body_composition`.
Unicità composita `(user_id, day)` — così due utenti possono avere lo stesso giorno, e la ri-sincronizzazione fa upsert invece di duplicare.

**Tabelle applicative** (v2): `users`, `invite_codes`, `user_goals`, `training_plans`, `daily_coach_cache`, `gamification_state`, `chat_conversations`, `chat_messages`, `ai_usage_log`.

### 3.2 `app/db/database.py` e `migrations/`
Engine URL-driven: SQLite in dev, Postgres in prod, stesso codice. Alembic gestisce l'evoluzione dello schema (`init_db()` resta solo per test e dev).

### 3.3 `app/queries.py`
Unico posto dove si legge il DB per la UI. Ogni funzione prende `user_id` e restituisce serie ordinate o dizionari pronti per i grafici. `coach_snapshot()` è la funzione chiave: aggrega tutto ciò che serve a readiness e insights in un solo dizionario None-safe.

---

## 4. Integrazioni

### 4.1 `app/garmin/client.py`
Registry di client Garmin **per-utente**: token cachato su disco in `data/garmin_tokens/{user_id}/`, lock per evitare login concorrenti, `validate_credentials()` usata in fase di registrazione.

### 4.2 `app/garmin/sync.py`
Scarica e salva le time-series (attività, sonno, training, wellness, corpo). Parsing **difensivo**: ogni campo mancante diventa `None`, un endpoint che fallisce non interrompe la sync degli altri.

### 4.3 `app/garmin/service.py`
Letture "snapshot" live non storicizzate (dispositivi, gear, record personali, race predictor, dettaglio attività) con cache in memoria TTL 5 min, keyed per utente.

---

## 5. Automazione

### 5.1 `app/scheduler.py`
APScheduler avviato nel lifespan di FastAPI. Nessun worker esterno: a <20 utenti sarebbe complessità inutile.

**Pipeline giornaliera, per ogni utente** (mattina presto, con stagger tra utenti):
```
sync Garmin → ricalcolo readiness/insights → gamification →
coaching AI (1 chiamata, cachata) → valutazione regole notifica → invio
```
Se la sync fallisce: retry con backoff; dopo N fallimenti l'utente riceve un avviso "ricollega Garmin".

---

## 6. Configurazione e deploy

### 6.1 `app/config.py`
Tutte le variabili d'ambiente in un unico oggetto `settings`.

| Gruppo | Variabili |
|---|---|
| Garmin | `GARMIN_TOKENSTORE` |
| Database | `DATABASE_URL` |
| Auth | `SESSION_SECRET`, `FERNET_KEY` |
| AI | `AI_PROVIDER`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL_LIGHT`, `CLAUDE_MODEL_HEAVY`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| Notifiche | `SMTP_*`, `TELEGRAM_BOT_TOKEN`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` |

> Le credenziali Garmin **non** sono più variabili d'ambiente: stanno cifrate nel DB, una per utente.

### 6.2 `app/cli.py`
CLI amministrativa: `create-invite`, `list-users`.

### 6.3 Deploy (VPS Hetzner)
`docker-compose.prod.yml`: `garmin-app` + `postgres` su volume dedicato, rete `caddy_proxy`.
Caddy: il blocco del sito vive in `sites/garmin.caddy`, importato con una riga dal Caddyfile esistente — **i blocchi del bot condominio non si toccano** (backup `.bak` prima di ogni modifica).

---

## 7. Test

`tests/` con pytest + httpx. Principi:
- I provider AI e Garmin **non vengono mai chiamati davvero**: fake injection o monkeypatch.
- Il test più importante del multi-utente: **l'utente A non deve mai vedere dati di B** (isolamento verificato a livello di query).
- I motori deterministici (readiness, insights, gamification, regole notifica) si testano con dizionari sintetici: nessun DB, nessuna rete.

---

## 8. Ordine in cui è stato costruito

| # | Fase | Perché in quest'ordine |
|---|---|---|
| 1 | Fondamenta multi-utente | Tocca ogni query e ogni route: farlo dopo avrebbe voluto dire rifare tutto |
| 2 | Scheduler + sync automatica | Servono dati freschi prima di costruirci sopra intelligenza |
| 3 | Obiettivi | Entrano nei prompt di tutto il resto |
| 4 | Provider AI + coaching cachato | Prima la singola chiamata giornaliera, più semplice della chat |
| 5 | Chat AI | Il pezzo più complesso, si appoggia su 3 e 4 |
| 6 | Notifiche + PWA | Hanno bisogno della pipeline di 2 e delle regole |
| 7 | Letture per pagina, mobile, piani, gamification | Il livello estetico e di prodotto |
| 8 | Admin | Governance |
| 9 | Deploy | Postgres, Caddy, re-sync |

---

## 9. Lezioni dal deploy

Due bug sono emersi **solo** su Postgres, mai in locale su SQLite. Sono il
motivo per cui conviene tenere il deploy vicino allo sviluppo invece di
rimandarlo alla fine.

| Bug | Perché sfuggiva in locale |
|---|---|
| `garmin_activity_id` era `Integer`: su Postgres sono 4 byte e gli id di Garmin hanno superato i 2,1 miliardi | Su SQLite gli interi sono a 64 bit: nessun overflow possibile |
| Il contatore dei fallimenti di sync scriveva senza `rollback()` | Postgres invalida l'intera transazione dopo un errore, SQLite no |

Entrambi hanno un test di regressione. Un terzo problema, i permessi del volume
(la v1 girava come root, la v2 come uid 1000), è annotato nella memoria di deploy.
