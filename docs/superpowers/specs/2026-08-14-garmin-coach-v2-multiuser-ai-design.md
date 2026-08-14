# Garmin Coach v2 — Multi-utente, Chat AI, Notifiche, Automazione

**Design document · 2026-08-14 · approvato tramite Q&A con Davide**

> Descrizione sistematica di ogni componente dell'app: [`docs/ARCHITETTURA.md`](../../ARCHITETTURA.md).
> Specifiche di interfaccia e pagine: [`PRD_GarminCoach.md`](../../../PRD_GarminCoach.md).

## 1. Visione

Trasformare il Garmin Connector da dashboard single-user in un **laboratorio da atleta multi-utente**:

- **Chat AI coach** dentro l'app, che conosce i dati recenti dell'utente e sa recuperare quelli storici on-demand.
- **Obiettivi configurabili** ("il laboratorio"): l'app sa sempre che obiettivo hai e ogni analisi/consiglio è orientato a quello.
- **Sync giornaliera automatica** da Garmin, senza intervento manuale.
- **Notifiche proattive** (Web Push PWA + email + Telegram) quando emerge qualcosa di importante.
- **Insights elaborati, non dati crudi**: ogni dashboard mostra trend, interpretazioni e azioni consigliate, prodotti dal motore deterministico + AI.
- **Multi-utente** con login via credenziali Garmin, registrazione a invito, deploy sulla VPS Hetzner esistente.

Principio di costo: **il determinismo fa i numeri, l'AI fa le parole**. Tutti i calcoli (readiness, trend, load, streak) restano in Python puro; l'AI riceve sintesi compatte e viene chiamata solo dove serve linguaggio o pianificazione. Budget target: **~5 €/mese** totali.

## 2. Decisioni architetturali (dal Q&A)

| Tema | Decisione |
|------|-----------|
| Login | **Credenziali Garmin = login.** Primo accesso con email/password Garmin crea l'account; password cifrata (Fernet, chiave in `.env`) + token Garmin cachati per-utente. Poi sessione cookie firmato. |
| Registrazione | **A invito** (codice invito richiesto alla prima registrazione), cerchia <20 utenti. |
| Frontend | **Evoluzione dello stack attuale** FastAPI + Jinja2 + Chart.js, interattività mirata con JS leggero (chat streaming, transizioni). Niente SPA. |
| Provider AI | **Provider-agnostico configurabile** (`AI_PROVIDER=claude|openai`), default Claude: Haiku per chat/coaching, Sonnet per generazione piani. |
| Costi AI | **Key centrale nel `.env`** + quote per utente (messaggi chat/giorno, coaching 1×/giorno cachato, piani limitati/mese). |
| Notifiche | **Tre canali**: Web Push (PWA), email transazionale (Brevo/Resend o SMTP), Telegram bot. Preferenze per-utente e per-tipo. |
| DB | **Postgres in produzione** (container dedicato), SQLite in locale per dev. **Alembic** per le migrazioni da subito. |
| VPS | Autorizzato: SSH+deploy, container Postgres, modifiche Caddy **in file separato importato** (mai toccare i blocchi del bot condominio; backup prima di ogni modifica), scheduler. |
| Budget | ~5 €/mese → quote iniziali: 30 msg chat/giorno/utente, coaching 1×/giorno cachato, 3 piani/mese/utente. |

### Vincolo Caddy (esplicitato)

Un solo IP → un solo listener su 80/443, oggi il Caddy del bot. Non è tecnicamente possibile un secondo Caddy indipendente sugli stessi porti. Mitigazione: config Garmin in `sites/garmin.caddy` importato dal Caddyfile del bot (una riga `import`, aggiunta una volta sola, con `.bak`). Alternativa futura se serve isolamento totale: secondo IPv4 Hetzner.

## 3. Architettura del sistema

```
Browser (PWA) ──HTTPS──> Caddy (esistente, config importata) ──> garmin-app (FastAPI)
                                                                    │
                              ┌─────────────────────────────────────┼───────────────┐
                              │                                     │               │
                        Postgres (nuovo container)          APScheduler        AI providers
                        (tutti i dati per-utente)           (in-process)       (Claude/OpenAI)
                              │                                     │               
                              │                             sync giornaliera,       
                              │                             coaching cache,         
                              │                             notifiche              
                              └── Garmin Connect (per-utente, token cache su volume)
```

Un solo processo applicativo (come oggi): FastAPI serve pagine + API, APScheduler gira in-process. Nessun worker/queue separato — a <20 utenti è overkill.

## 4. Multi-utente e autenticazione

### 4.1 Modello utente

```python
class User(Base):
    id, garmin_email (unique), garmin_password_encrypted,  # Fernet
    display_name, created_at, is_admin,
    ai_quota_chat_daily (default 30), timezone,
    notify_prefs_json      # {canale: {tipo_evento: bool}}, chat_id telegram, push subscriptions

class InviteCode(Base):
    id, code, created_by, used_by, used_at, expires_at
```

- **Tutte le tabelle dati esistenti** (`activities`, `sleep_records`, `training_metrics`, `daily_wellness`, `body_composition`) ricevono `user_id` FK + indice composito `(user_id, data)`.
- Token Garmin per-utente: `data/garmin_tokens/{user_id}/` (volume Docker già esistente).
- Il singleton `app/garmin/client.py` diventa un **registry di client per-utente** con lock per evitare login concorrenti.

### 4.2 Flusso di accesso

1. `/login`: form email+password Garmin. Se l'utente esiste → verifica password contro l'hash locale (bcrypt della password Garmin, oltre alla copia cifrata Fernet per la sync) → sessione.
2. Se non esiste → richiesta **codice invito** → tentativo di login reale su Garmin (valida le credenziali) → crea utente, salva credenziali cifrate, avvia prima sync in background.
3. Sessione: cookie firmato (itsdangerous, già in dipendenza con FastAPI/starlette), scadenza 30 giorni, middleware che protegge tutte le pagine tranne `/login` e asset statici.
4. Cambio password Garmin → il login locale fallisce solo se anche Garmin la rifiuta: al fallimento locale si ritenta contro Garmin e si aggiorna l'hash (self-healing).

Admin (Davide): flag `is_admin` → pagina `/admin` minimale (utenti, inviti, consumo AI, stato sync).

## 5. Database e migrazioni

- **Alembic da subito**: baseline autogenerata dallo schema attuale, poi migrazione "multiuser" (colonna `user_id`, nuove tabelle). I dati esistenti sul server vengono assegnati all'utente Davide creato in migrazione.
- Dev locale: SQLite (com'è oggi). Prod: `DATABASE_URL=postgresql+psycopg://...`. Il codice è già URL-driven.
- Migrazione dati prod: **re-sync da Garmin** dopo il passaggio a Postgres (Garmin è la source of truth; più semplice e pulito di uno script di travaso SQLite→Postgres).

## 6. Sync automatica giornaliera

- **APScheduler** in-process (`app/scheduler.py`), avviato nel lifespan di FastAPI.
- Job `daily_sync`: ogni mattina (es. 06:30 ora utente, con stagger di qualche minuto tra utenti per non martellare Garmin) esegue la sync esistente per ogni utente attivo.
- Dopo la sync di ciascun utente, in ordine: **ricalcolo readiness/insights → coaching AI giornaliero (cache) → valutazione regole di notifica → invio notifiche**.
- Job `retry`: se la sync di un utente fallisce (Garmin down, credenziali invalide), retry con backoff; dopo N fallimenti notifica l'utente ("ricollegati a Garmin").
- La sync manuale (bottone "Sincronizza") resta e usa lo stesso codice.

## 7. Livello AI

### 7.1 Provider configurabile

`app/ai/provider.py` completa l'astrazione esistente:

- `ClaudeProvider` (Anthropic SDK): modello "light" (Haiku) e "heavy" (Sonnet) configurabili da env.
- `OpenAIProvider`: equivalente (classe mini / classe standard).
- Selezione da `AI_PROVIDER`; ogni provider implementa `chat(messages, tools)`, `coach(context)`, `generate_plan(context)`.

### 7.2 Chat AI coach — il cuore del progetto

**Architettura: agente con tool-calling su funzioni deterministiche.** Questo risolve strutturalmente il problema del "filtro dati": non decidiamo noi a priori cosa mandare — il modello riceve di default solo una **sintesi compatta recente** e, se la conversazione tocca periodi più vecchi o dettagli, chiama i tool per farsi dare esattamente ciò che serve.

- **System prompt**: ruolo coach + profilo utente + **obiettivo attivo con parametri** + snapshot compatto (readiness oggi, trend 7/28gg pre-calcolati, ultimo allenamento, piano attivo e settimana corrente). Poche centinaia di token, quasi tutto pre-calcolato in Python.
- **Tools** (funzioni Python che interrogano il DB, output compatto e già aggregato):
  - `get_daily_summary(date_from, date_to)` — wellness+sonno aggregati
  - `get_activities(date_from, date_to, type?)` — lista attività con metriche chiave
  - `get_activity_detail(id)`
  - `get_metric_trend(metric, date_from, date_to, granularity)` — serie aggregata (settimanale/mensile per periodi lunghi: mai centinaia di punti raw)
  - `get_personal_records()`, `get_goal_and_plan()`
- **Persistenza**: `ChatConversation` + `ChatMessage` per-utente; si riparte dalle conversazioni passate. Contesto inviato: ultimi ~20 messaggi (le conversazioni lunghe vengono troncate, non riassunte — YAGNI).
- **UI**: pagina `/chat` con streaming SSE (endpoint FastAPI `StreamingResponse`), bolle, indicatore "il coach sta consultando i tuoi dati…" quando gira un tool. Accessibile anche da un widget nella pagina `/coach`.
- **Quota**: contatore messaggi/giorno per utente; superata → messaggio gentile con reset a mezzanotte.
- **Modello**: quello "light" (Haiku). Max ~4-6 tool call per messaggio.

### 7.3 Coaching giornaliero e insights nelle dashboard

- Già esistente il motore deterministico (readiness, insights). Si estende con la **cache giornaliera AI** (`DailyCoachCache`): 1 chiamata light/giorno/utente che produce messaggio del coach + nota tattica allenamento, orientati all'obiettivo attivo.
- **Insight contestuali nelle pagine dati**: ogni pagina (sonno, salute, performance, corpo) mostra 1-3 card insight *deterministiche* pertinenti alla pagina (dal motore regole, filtrate per dominio) — trend, interpretazione, azione consigliata. Zero costo AI: le regole producono il testo, l'AI giornaliera resta solo su `/coach`.

### 7.4 Piani di allenamento

Come da design precedente (AI_COACHING_DESIGN.md): generazione col modello "heavy" (Sonnet), output JSON strutturato, salvato in `TrainingPlan`, adattamento giornaliero deterministico via readiness. Quota: 3 generazioni/mese/utente.

### 7.5 Controllo costi

- `AIUsageLog` (user_id, giorno, tipo chiamata, token in/out) → pagina admin con spesa stimata.
- Quote: 30 msg chat/giorno, 1 coaching/giorno (cache), 3 piani/mese. Configurabili per utente dall'admin.
- Prompt caching Anthropic sul system prompt della chat (stabile per gran parte della giornata).

## 8. Obiettivi — "il laboratorio"

Pagina `/goals` dove l'utente configura il proprio obiettivo attivo e i parametri:

```python
class UserGoal(Base):
    id, user_id, goal_type,        # gara_5k|10k|half|marathon|peso|forma_generale|sonno|custom
    title, target_date,
    params_json,                   # es. {"target_time": "45:00", "target_weight": 72, "weekly_km": 40, "sleep_hours": 7.5}
    priority_notes,                # testo libero: "privilegia il recupero", "ginocchio delicato"
    active, created_at
```

- Un solo obiettivo **attivo** alla volta (i passati restano in archivio con esito).
- L'obiettivo attivo entra: nel system prompt della chat, nel prompt del coaching giornaliero, nella generazione piani, e nelle regole di notifica (es. "gara tra 7 giorni → inizia lo scarico").
- UI: form curato con card per tipo obiettivo, parametri dinamici per tipo, data target, note libere.

## 9. Notifiche

### 9.1 Architettura

`app/notifications/` con un **dispatcher** e canali plugin:

- `webpush.py` — Web Push VAPID (lib `pywebpush`), subscription salvate per-utente. PWA: `manifest.json` + service worker (già serve anche per installabilità su telefono).
- `email.py` — SMTP configurabile da env (parte con Brevo free tier o Gmail SMTP; astrazione minima, si cambia da `.env`).
- `telegram.py` — bot API (solo `sendMessage`, niente framework); collegamento con deep-link `t.me/<bot>?start=<codice-utente>`.

### 9.2 Eventi che generano notifiche (regole deterministiche, valutate post-sync)

- Readiness molto basso / molto alto (giorno chiave)
- Warning overtraining (load ratio alto)
- Trend negativo sonno persistente
- Sessione chiave del piano oggi / adattamento suggerito
- Gara vicina (countdown/scarico)
- Sync fallita ripetutamente
- Riepilogo settimanale (opzionale, domenica sera)

Ogni utente sceglie in `/settings` quali **tipi** ricevere su quali **canali**. Default conservativo: solo warning importanti. Dedup: stessa notifica non ripetuta entro N giorni.

## 10. UI/UX

- Si estende il design system dark già avviato (tokens, ring SVG, blend Oura/Whoop). Priorità estetica alta: la skill frontend-design/ui-ux-pro-max guiderà le nuove pagine.
- **Nuove pagine**: `/login` (+invito), `/chat`, `/goals`, `/settings` (notifiche, account), `/admin`, `/plan` (dal PRD).
- **Pagine esistenti**: restyling secondo PRD §5 + card insight contestuali (§7.3) accanto ai grafici.
- **PWA**: manifest, icone, service worker (cache statici + push). Su iOS: banner "Aggiungi a Home" per abilitare le push.
- Topbar/sidebar: avatar utente, stato sync, accesso chat sempre visibile.

## 11. Deploy (VPS Hetzner)

- `docker-compose.prod.yml`: aggiunta servizio `postgres:16-alpine` con volume `garmin_pg_data`, healthcheck; `garmin-app` dipende da esso. Rete `caddy_proxy` invariata.
- Caddy: creare `sites/garmin.caddy` con il blocco garmin (rimozione `basic_auth` — ora c'è il login applicativo); nel Caddyfile del bot una sola riga `import`. Backup `.bak` prima.
- Secrets nuovi in `.env.prod`: `FERNET_KEY`, `SESSION_SECRET`, `ANTHROPIC_API_KEY` (e/o `OPENAI_API_KEY`), SMTP, `TELEGRAM_BOT_TOKEN`, chiavi VAPID.
- Dati esistenti: re-sync da Garmin post-migrazione (Garmin è la source of truth).
- Scheduler: in-process, nessun cron di sistema necessario.

## 12. Testing

- Si estende la suite pytest esistente (httpx): auth flow (invito, login, sessione), isolamento dati tra utenti (test critico: l'utente A non vede mai dati di B), quote AI, motore regole notifiche, tool della chat (con provider fake), migrazioni Alembic su DB vuoto e su DB v1.
- I provider AI reali mai chiamati nei test: `FakeProvider` con risposte canned.

## 13. Fasi di implementazione (ordine di build)

1. **Fondamenta multi-utente**: Alembic + modelli User/Invite + auth/sessioni + `user_id` ovunque + registry client Garmin per-utente + login/onboarding UI. *(La più invasiva: tutto il resto ci si appoggia.)*
2. **Scheduler + sync automatica** con pipeline post-sync.
3. **Obiettivi** (`/goals` + integrazione nei prompt/regole).
4. **Provider AI reali** (Claude+OpenAI) + coaching giornaliero cachato + usage log/quote.
5. **Chat AI** (tools, persistenza, SSE streaming, UI).
6. **Notifiche** (dispatcher + 3 canali + regole + `/settings` + PWA/push).
7. **Insight contestuali + restyling pagine** secondo PRD + `/plan`.
8. **Admin page**.
9. **Deploy**: Postgres, Caddy import, secrets, re-sync, smoke test.

Ogni fase: piano dettagliato → TDD → review → commit. Le fasi 3-8 sono relativamente indipendenti tra loro una volta chiusa la 1-2.

## 14. Out of scope

- App mobile nativa / notifiche push native iOS senza PWA
- Import da Strava/Polar
- Pagamenti/billing per utenti
- Coach vocale, immagini, mappe GPS
- Riassunto automatico conversazioni chat lunghe
- Scaling oltre ~20 utenti (queue, worker separati, rate limiting distribuito)
