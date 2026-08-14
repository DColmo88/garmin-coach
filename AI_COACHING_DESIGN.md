# AI Coaching — Design Document

> **Obiettivo:** trasformare il Garmin Connector da semplice dashboard di lettura a un **coach personale AI** che ti dice ogni giorno cosa fare, perché farlo, e ti tiene motivato con gamification e piani strutturati.

> **⚠️ Aggiornamento 2026-08-14 — v2:** il progetto è evoluto in un sistema **multi-utente** con **chat AI coach**, **obiettivi configurabili**, **sync giornaliera automatica** e **notifiche** (Web Push PWA + email + Telegram). Le decisioni architetturali complete sono in `docs/superpowers/specs/2026-08-14-garmin-coach-v2-multiuser-ai-design.md` — quella spec **prevale** su questo documento dove i due divergono. Questo file resta valido per: readiness engine, insights engine, gamification, training plans.
>
> Per la descrizione sistematica di ogni componente dell'app: **`docs/ARCHITETTURA.md`**.

---

## 1. Visione

L'utente apre l'app e vede:

1. **"Oggi il tuo score di prontezza è 78/100 — Allenamento moderato consigliato"**
2. Tre insight personalizzati dai suoi dati ("la tua FC a riposo è scesa di 4 bpm in 3 settimane — sei in forma ascendente 🔥")
3. La sessione di allenamento suggerita per oggi (tipo, durata, zone)
4. Il suo streak attivo + progress verso l'obiettivo corrente

**Tutto basato esclusivamente sui propri dati reali.**

---

## 2. Dati disponibili per il coaching

Da ciò che Garmin fornisce, i segnali più potenti per il coaching sono:

| Segnale | Fonte DB | Utilizzo |
|---------|----------|---------|
| Sleep score + fasi | `sleep_records` | Recupero notturno |
| HRV status + weekly avg | `training_metrics` | Stato nervoso, stress/recupero |
| Body Battery max/min | `daily_wellness` | Energia disponibile giornaliera |
| Training load | `training_metrics` | Carico acuto/cronico |
| Readiness score | `training_metrics` | Prontezza Garmin (se disponibile) |
| Resting HR trend | `daily_wellness` + `sleep_records` | Fitness trend |
| VO2max trend | `training_metrics` | Fitness aerobica |
| Training status | `training_metrics` | "Productive"/"Overreaching"/ecc. |
| Attività recenti | `activities` | Volume, intensità, tipologia |
| Stress giornaliero | `daily_wellness` | Carico allostatic totale |

---

## 3. Readiness Score (engine Python, no AI)

Un punteggio composito **0–100** calcolato ogni giorno da più segnali:

```
Readiness Score =
  sleep_score          × 0.30   (0-100 da Garmin o calcolato)
  hrv_factor           × 0.25   (balanced=100, unbalanced=40, missing=65)
  body_battery_max     × 0.20   (0-100, usa body_battery_high)
  load_ratio_factor    × 0.15   (acuto/cronico: 0.8-1.2 → 80, >1.5 → 30)
  resting_hr_factor    × 0.10   (trend in calo → 80, salita → 40)
```

**Classi di output:**

| Score | Etichetta | Raccomandazione |
|-------|-----------|-----------------|
| 80–100 | 🔥 Pronto | Allenamento intenso / sessione chiave |
| 65–79 | ✅ Buono | Allenamento moderato |
| 50–64 | 🟡 Discreto | Corsa easy / attività leggera |
| 35–49 | 🔵 Stanco | Recovery run o riposo attivo |
| 0–34 | ❌ Riposo | Riposo completo |

Questo engine è **deterministico, trasparente e istantaneo** — nessuna chiamata AI necessaria per il numero.

---

## 4. Insights Engine (ibrido: regole + AI)

### 4a. Regole deterministiche (sempre attive)

Detect automatico di pattern nei dati ultimi 7/30 giorni:

```python
# Esempi di regole
- resting_hr_7d < resting_hr_30d - 3:  "FC a riposo -Xbpm vs mese scorso → fitness in crescita 📈"
- sleep_score_avg_7d < 65:             "Qualità del sonno bassa questa settimana → priorità recupero"
- training_load > chronic_load * 1.4:  "⚠️ Carico acuto alto — rischio overtraining"
- vo2max_latest > vo2max_4w_ago:        "VO2max migliorato di X.X → sei più forte di un mese fa"
- days_since_last_run > 5:             "5 giorni senza corsa — il corpo è riposato, momento perfetto"
- consecutive_active_days >= 7:        "🔥 7 giorni consecutivi attivi!"
- sleep_deep_pct < 0.15:              "Sonno profondo sotto 15% — considera rituale serale migliore"
- body_battery_low_avg < 20:          "Body Battery quasi a zero ogni sera — stai facendo troppo"
```

Queste vengono generate in Python puro — **zero latenza, zero costo API**.

### 4b. Coaching narrativo (AI, chiamata 1x/giorno con cache)

Il prompt inviato all'AI (provider configurabile: Claude **o** OpenAI, default Claude Haiku) viene costruito con i dati reali **e con l'obiettivo attivo dell'utente** (v2) e produce:

```
1. Messaggio del giorno (2-3 frasi, personale, con dati specifici)
2. Allenamento suggerito (tipo, durata, zone FC, note)
3. Focus della settimana (es. "incrementa il volume leggero")
4. Warning se emergono pattern preoccupanti
```

**Il coaching AI viene chiamato una volta al giorno e cachato** — non a ogni richiesta.

---

## 5. Gamification

### XP (Experience Points)

| Azione | XP |
|--------|----|
| Qualsiasi attività completata | 50 + 1/km |
| Sleep score ≥ 80 | 40 |
| Steps ≥ obiettivo giornaliero | 25 |
| Attività 5+ giorni in una settimana | 100 bonus |
| Nuovo personal record | 200 |
| Training Readiness ≥ 80 | 30 |
| Settimana senza overtraining | 80 |

### Livelli

| Livello | XP richiesto | Badge |
|---------|-------------|-------|
| Beginner | 0 | 🌱 |
| Athlete | 1.000 | 🏅 |
| Dedicated | 5.000 | 🥈 |
| Elite | 15.000 | 🥇 |
| Legend | 40.000 | 🏆 |

### Streak System

- **Streak attività**: giorni consecutivi con almeno un'attività
- **Streak sonno**: notti consecutive con score ≥ 70
- **Streak steps**: giorni consecutivi con steps ≥ goal
- **Streak no-overtraining**: settimane consecutive con carico equilibrato

I streak si azzerano (con grazia: si mostra il "record" e si offre di battere il prossimo).

### Badge / Achievement

Esempi:
- 🏃 "First 10K" — prima corsa ≥ 10 km
- ⚡ "Speed Demon" — primo interval training
- 🌙 "Sleep Champion" — 7 notti consecutive score ≥ 80
- 📈 "Improving" — VO2max +2 in 30 giorni
- 💪 "Consistent" — 30 giorni con almeno 3 attività/settimana
- 🔥 "On Fire" — streak attività ≥ 14 giorni
- 🧘 "Recovery Master" — Body Battery non sotto 30 per 7 giorni

---

## 6. Training Plans

### Obiettivi disponibili

1. **Corsa — 5K** (principiante/intermedio)
2. **Corsa — 10K**
3. **Mezza maratona**
4. **Maratona**
5. **Forma generale** (mix running + wellness)
6. **Recupero** (piano desessione post-gara/infortunio)

### Come funziona

Il piano AI viene generato una volta e salvato nel DB. Ogni giorno il coach controlla:
- Dove sei nel piano
- Come stai (readiness score)
- Se adattare la sessione di oggi (es. spostare intervallo a domani se readiness < 50)

**Prompt per il piano:**
```
Sei un coach running certificato. Crea un piano di N settimane per [obiettivo].
Dati atleta:
- VO2max attuale: X
- Km/settimana ultimi 30 gg: Y
- Training status Garmin: Z
- Obiettivo data gara: [data]

Il piano deve essere:
- Progressivo (non aumentare volume >10%/settimana)
- Con tipologie di sessione specifiche (easy run, tempo, interval, long run, rest)
- Realistico per il livello attuale
- In formato JSON strutturato (settimana/giorno/sessione)
```

---

## 7. Nuova architettura — file da aggiungere (aggiornata v2)

```
app/
├── ai/
│   ├── readiness.py        # ✅ FATTO — Readiness Score engine (Python puro)
│   ├── insights.py         # ✅ FATTO — Insight rules engine (Python puro)
│   ├── coaching.py         # ✅ base fatta — Daily coaching (prompt + cache 1/giorno)
│   ├── chat.py             # v2 — agente chat con tool-calling sui dati DB
│   ├── tools.py            # v2 — tool deterministici per la chat (query aggregate)
│   ├── training_plan.py    # Plan generator + day resolver
│   ├── context.py          # ESTESO con dati trend + comparative
│   └── provider.py         # ESTESO: ClaudeProvider + OpenAIProvider, chat/coach/plan
│
├── auth/                   # v2 — security (Fernet/bcrypt), sessioni, login a invito
├── notifications/          # v2 — dispatcher + canali webpush/email/telegram
├── scheduler.py            # v2 — APScheduler: sync giornaliera + pipeline post-sync
│
├── gamification/
│   ├── engine.py           # Calcola XP, streak, badge
│   └── levels.py           # Definizione livelli e badge
│
├── db/
│   └── models.py           # v2: User, InviteCode, UserGoal, TrainingPlan,
│                           #     DailyCoachCache, GamificationState,
│                           #     ChatConversation, ChatMessage, AIUsageLog
│
├── routers/
│   ├── pages.py            # /coach ✅, /plan, /goals, /settings, /admin
│   ├── auth.py             # v2 — /login, /logout
│   └── chat.py             # v2 — /chat + streaming SSE
│
└── templates/
    ├── coach.html           # ✅ FATTO — pagina coaching principale
    ├── login.html / chat.html / goals.html / settings.html / plan.html
    └── ...
```

### Nuovi modelli DB (aggiornati v2 — tutti con `user_id` FK)

```python
class UserGoal(Base):
    __tablename__ = "user_goals"
    id, user_id, goal_type, title, target_date,
    params_json,        # es. {"target_time": "45:00", "weekly_km": 40}
    priority_notes,     # testo libero: "privilegia il recupero"
    active, created_at

class TrainingPlan(Base):
    __tablename__ = "training_plans"
    id, user_id, goal_id, plan_json, generated_at, weeks_total

class DailyCoachCache(Base):
    __tablename__ = "daily_coach_cache"
    id, user_id, day, readiness_score, readiness_label,
    coach_message, workout_suggestion, insights_json, generated_at

class GamificationState(Base):
    __tablename__ = "gamification_state"
    id, user_id, total_xp, current_level, current_streak,
    longest_streak, badges_json, last_updated

class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    id, user_id, title, created_at, updated_at

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id, conversation_id, role, content, created_at

class AIUsageLog(Base):
    __tablename__ = "ai_usage_log"
    id, user_id, day, kind,          # chat|coach|plan
    tokens_in, tokens_out
```

---

## 8. Pagina `/coach` — UX

```
┌─────────────────────────────────────────────────────┐
│  🌤️  Buongiorno, Davide!    [Thu 18 Jun]            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  PRONTEZZA OGGI                                     │
│  ████████████████░░░  78/100  ✅ Buono              │
│  "Hai dormito 7h con score 74. HRV stabile.         │
│   Allenamento moderato consigliato."                │
│                                                     │
├─────────────────────────────────────────────────────┤
│  ALLENAMENTO SUGGERITO                              │
│  🏃 Corsa Easy 45–50 min                            │
│  Zona 2 (< 145 bpm) · Passo ~5:30/km               │
│  "Mantieni la conversazione — oggi costruiamo       │
│   la base aerobica."                               │
├─────────────────────────────────────────────────────┤
│  INSIGHT DI OGGI                                    │
│  📈 FC riposo -3 bpm vs 30gg fa — in forma          │
│  ⚠️  Carico settimana alta — domani rest o easy     │
│  🌙  Sonno profondo basso — prova a dormire prima   │
├─────────────────────────────────────────────────────┤
│  IL TUO PROGRESSO                                   │
│  🔥 Streak: 9 giorni   🥈 Dedicated (3.240 XP)     │
│  [████████░░] Piano 10K — Settimana 4/8             │
└─────────────────────────────────────────────────────┘
```

---

## 9. Roadmap di implementazione (aggiornata v2)

> ✅ Già fatto: readiness engine, insights engine, coaching deterministico, pagina `/coach` con design system dark.

La roadmap v2 completa è nella spec (`docs/superpowers/specs/2026-08-14-garmin-coach-v2-multiuser-ai-design.md`, §13):

1. **Fondamenta multi-utente** — Alembic, User/InviteCode, auth a sessioni, `user_id` ovunque, client Garmin per-utente, login UI *(piano: `docs/superpowers/plans/2026-08-14-fase1-multiutente.md`)*
2. **Scheduler + sync automatica giornaliera** con pipeline post-sync
3. **Obiettivi** (`/goals` — il "laboratorio": tipo, parametri, data target, note)
4. **Provider AI reali** (Claude + OpenAI configurabili) + coaching giornaliero cachato + quote/usage log
5. **Chat AI coach** (tool-calling sui dati, persistenza conversazioni, streaming SSE)
6. **Notifiche** (Web Push PWA + email + Telegram, regole deterministiche, preferenze utente)
7. **Insight contestuali + restyling pagine** secondo PRD + `/plan` (include gamification §5)
8. **Admin page** (utenti, inviti, consumo AI)
9. **Deploy Hetzner** (Postgres, Caddy in file importato, re-sync dati)

---

## 10. Note tecniche

- **Claude Haiku** è ideale per i coaching message giornalieri e la chat (veloce, economico). **Claude Sonnet** per la generazione del piano (complessità maggiore). Provider alternativo OpenAI configurabile da `.env`.
- **Controllo costi (v2)**: key API centrale + quote per utente (30 msg chat/giorno, coaching 1×/giorno cachato, 3 piani/mese), `AIUsageLog` per il monitoraggio, prompt caching sul system prompt. Budget target ~5 €/mese.
- Il coaching AI va cachato in `DailyCoachCache` — si rigenera solo se il giorno cambia o si forza il refresh.
- Le regole deterministiche in `insights.py` devono avere priorità: mostrare i 3 più rilevanti (non tutti quelli attivi).
- La gamification non richiede input utente: si calcola in automatico dal DB ad ogni sync.
- Il Training Plan in JSON strutturato permette all'interfaccia di renderizzare un calendario interattivo (settimane/giorni).
