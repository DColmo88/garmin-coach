# PRD — Garmin Coach
**Product Requirements Document · v1.0 · Giugno 2026**

---

## 1. Executive Summary

**Garmin Coach** è una web app personale che trasforma i dati di Garmin Connect in un coach atletico intelligente. L'utente non deve più interpretare numeri grezzi: l'app calcola ogni giorno un punteggio di prontezza, suggerisce l'allenamento ottimale, mostra insight personalizzati sui trend, e tiene l'utente motivato con gamification e piani di allenamento strutturati.

**Stack tecnologico:** FastAPI + Jinja2 (server-side HTML) + Chart.js. Single-user, self-hosted. Dark mode nativa. Responsive (desktop-first, mobile-friendly).

---

## 2. Utente

Un singolo utente, sportivo amatoriale con orologio Garmin (running principalmente, possibile ciclismo). Usa l'app come cruscotto personale e strumento di pianificazione. Vuole capire rapidamente come sta, cosa fare oggi, e vedere i progressi nel tempo. Non è un data scientist — vuole risposte, non tabelle.

---

## 3. Design Direction

| Parametro | Specifica |
|-----------|-----------|
| **Tema** | Dark mode (sfondo ~#0f1117, superfici #1a1d27) |
| **Accent** | Un colore primario vivido — es. teal (#00d4aa) o blue elettrico (#4f8ef7) — da definire |
| **Tipografia** | Sans-serif moderna (Inter, Geist, DM Sans) — leggibile su dati densi |
| **Layout** | Sidebar fissa a sinistra (desktop) · bottom nav (mobile) |
| **Cards** | Bordi sottili, leggero raggio, shadow soft — niente bordi pesanti |
| **Grafici** | Chart.js, linee smooth, colori coerenti con il tema |
| **Tone of voice** | Diretto, energico, personale — parla all'utente in prima persona |

**Reference app per mood:** Whoop, Oura Ring, Apple Fitness+ (dark, dati leggibili, gerarchia visiva forte).

---

## 4. Architettura dell'informazione

### Navigazione principale (sidebar)

```
🏠  Coach          ← NUOVA — landing page principale
◎   Panoramica     ← esistente, ridimensionata a "tutti i KPI"
🏃  Attività       ← esistente
🌙  Sonno          ← esistente
❤️  Salute         ← esistente
📈  Performance    ← esistente
⚖️  Corpo          ← esistente
🎯  Piano          ← NUOVA — training plan attivo
```

> **Nota UX:** "Dispositivi" viene rimosso dalla nav principale (non rilevante per il coaching). "AI Insights" viene integrato nel Coach e Piano — non ha più bisogno di una pagina separata.

---

## 5. Specifiche per pagina

---

### 5.1 `/coach` — Coach (nuova, landing principale)

**Scopo:** la pagina che l'utente apre ogni mattina. In 5 secondi deve sapere come sta e cosa fare.

**Struttura della pagina:**

#### Sezione A — Hero: Prontezza del giorno
Un blocco grande, visivamente dominante. Contiene:
- **Readiness Score**: numero grande (0–100) con indicatore circolare/gauge
- **Etichetta stato**: testo leggibile — es. "🔥 Pronto per un allenamento intenso" / "✅ Forma buona — allenamento moderato" / "🟡 Stanco — vai leggero oggi" / "❌ Riposo consigliato"
- **Breakdown visivo** (piccolo, sotto il numero): 5 pill/barre che mostrano i contribuenti al punteggio — Sonno, HRV, Body Battery, Carico, FC riposo — ognuno con un valore e un colore (verde/giallo/rosso)
- **Messaggio del coach** (AI): 2-3 frasi personalizzate con dati specifici. Es: *"Hai dormito 7h 12m con uno score di 78. L'HRV è stabile e il Body Battery ha raggiunto 91 stanotte — sei in forma. Mantieni il ritmo."*

#### Sezione B — Allenamento suggerito oggi
Card con sfondo leggermente più chiaro per distinguersi. Contiene:
- **Tipo attività**: icona + label (es. "🏃 Corsa easy" / "🚴 Bici rigenerativa" / "💤 Riposo attivo")
- **Durata**: es. "40–50 minuti"
- **Intensità / zona FC target**: es. "Zona 2 — sotto 148 bpm" — con piccolo indicatore visivo delle 5 zone
- **Nota tattica** (AI, 1-2 frasi): es. *"Focus sul respiro nasale. Nessun effort — l'obiettivo è accumulare volume aerobico senza alzare il carico."*
- **CTA**: pulsante "Segna come completato" (futuro) o semplicemente link a → Piano

#### Sezione C — Insight del giorno
3 card orizzontali (o verticali su mobile), ognuna con:
- **Icona + titolo breve**: es. "📈 FC in miglioramento"
- **Testo**: es. "La tua FC a riposo è scesa di 3 bpm rispetto al mese scorso — segnale chiaro di adattamento aerobico."
- **Colore bordo**: verde (positivo), arancio (attenzione), rosso (warning)

Tipologie di insight possibili:
- Trend FC a riposo (↓ o ↑)
- Qualità sonno ultima settimana
- Training load: equilibrato / alto / basso
- VO2max trend
- Streak attivo
- Pattern "dormi meglio dopo easy run"
- Warning overtraining
- "5 giorni senza corsa — il corpo è riposato"

#### Sezione D — Strip Gamification
Barra compatta in basso alla pagina (o sidebar destra). Contiene:
- **🔥 Streak attività**: "9 giorni consecutivi"
- **XP settimana**: "240 XP questa settimana"
- **Livello attuale**: badge/chip con nome livello (Beginner / Athlete / Dedicated / Elite / Legend)
- **Barra XP verso prossimo livello**: progress bar lineare
- **Badge recenti**: 2-3 icone degli ultimi achievement sbloccati

---

### 5.2 `/` — Panoramica (esistente, restyling)

**Scopo:** tutti i KPI recenti in un unico colpo d'occhio. Non è la home principale — è il "cruscotto tecnico" per chi vuole i dati.

**Struttura:**
- **Row KPI**: 6-8 card numero (passi, FC riposo, Body Battery max, sleep score, VO2max, readiness, stress, training status). Design: numero grande, label piccola, freccia trend (↑↓ vs 7gg fa).
- **Grid grafici 2×2**: Passi 28gg · FC a riposo 28gg · Sleep score 28gg · VO2max storico
- **Tabella attività recenti**: 5 righe, cliccabili — Data, Tipo, Distanza, Durata, FC media

**Miglioramenti UX rispetto all'attuale:**
- Aggiungere freccia trend su ogni KPI card (confronto con 7gg fa)
- Bottone "Sincronizza" più prominente con feedback visivo (spinner + timestamp "Ultimo sync: 2h fa")

---

### 5.3 `/activities` — Attività (esistente, restyling)

**Scopo:** storico allenamenti + distribuzione per tipo.

**Struttura:**
- **Filtri top**: pill selezionabili per tipo attività (Tutti / Corsa / Bici / Nuoto / Palestra)
- **Grafico distribuzione**: doughnut chart con % per tipo attività (ultimi 90gg)
- **Grafico volume settimanale**: bar chart km o minuti per settimana (ultimi 12 settimane)
- **Tabella attività**: paginata, colonne: Data · Tipo (tag colorato) · Distanza · Durata · Passo · FC media · Aerobic TE
- Ogni riga cliccabile → `/activities/{id}`

**`/activities/{id}` — Dettaglio attività:**
- Mappa placeholder (o vuota se no GPS)
- KPI della sessione: distanza, durata, passo, FC media/max, dislivello, calorie, TE aerobico/anaerobico
- Grafico FC nel tempo (se disponibile)
- Split per km/lap in tabella

---

### 5.4 `/sleep` — Sonno (esistente, restyling)

**Scopo:** capire la qualità del sonno nel tempo.

**Struttura:**
- **KPI top**: durata media 7gg · sleep score medio 7gg · % sonno profondo · % REM
- **Grafico fasi impilato**: stacked bar per notte (profondo/leggero/REM/sveglio) — ultimi 28 giorni
- **Grafico sleep score**: linea temporale ultimi 28gg
- **Tabella notti**: Data · Durata totale · Profondo · REM · Leggero · Score · FC riposo notturna

---

### 5.5 `/health` — Salute (esistente, restyling)

**Scopo:** metriche di benessere quotidiano.

**Struttura — tab o sezioni scrollabili:**
- **Frequenza cardiaca**: FC riposo (linea), FC min/max (area) — 28gg
- **Stress**: linea stress medio giornaliero — 28gg
- **Body Battery**: area chart max/min giornalieri — 28gg
- **Passi**: bar chart passi + linea obiettivo — 28gg
- **SpO₂ e respirazione**: linee — 28gg
- **Minuti intensità**: bar chart moderata/vigorosa — 28gg

---

### 5.6 `/performance` — Performance (esistente, restyling)

**Scopo:** metriche atletiche e indicatori di fitness.

**Struttura:**
- **Grafici 2×2**: VO2max (corsa/bici) · Training load · HRV settimanale · Training readiness
- **Card Training Status**: badge grande con stato attuale ("Productive" / "Overreaching" / ecc.) + spiegazione testuale
- **Record personali**: tabella ordinabile
- **Race predictor**: 4 card (5K / 10K / Half / Marathon) con tempo stimato e indicatore "migliorato/peggiorato" vs mese fa

---

### 5.7 `/body` — Corpo (esistente, restyling)

**Scopo:** composizione corporea nel tempo.

**Struttura:**
- **KPI**: peso attuale · BMI · % grasso · massa muscolare
- **Grafico peso**: linea temporale
- **Grafico composizione**: area stacked (grasso / muscolo / altro)
- *Nota: pagina minimale se non si ha la bilancia Garmin Index*

---

### 5.8 `/plan` — Piano di allenamento (nuova)

**Scopo:** visualizzare e gestire il piano di allenamento attivo, generato dall'AI.

**Stato "nessun piano attivo":**
- CTA grande: "Crea il tuo piano di allenamento"
- Card selezionabili con obiettivo:
  - 🏃 Corsa 5K (principiante)
  - 🏃 Corsa 10K
  - 🏃 Mezza maratona
  - 🏃 Maratona
  - 💪 Forma generale
  - 🔄 Piano recupero
- Dopo selezione: form con data obiettivo (gara) → bottone "Genera piano" → loading → piano generato

**Stato "piano attivo":**

#### Sezione A — Header piano
- Nome piano + data gara target
- Settimana corrente: "Settimana 4 di 10"
- Progress bar del piano
- Distanza totale percorsa vs. target piano

#### Sezione B — Calendario settimana corrente
Vista 7 colonne (Lun–Dom). Ogni giorno ha una card:
- **Giorno con sessione**: tipo sessione (icona + label), durata, intensità, zona FC. Se il readiness score è basso, la card mostra ⚠️ "Considera di spostare a domani"
- **Giorno di riposo**: label "Riposo" con colore neutro
- **Sessione completata**: checkmark + verde

#### Sezione C — Piano completo (accordion)
Settimana per settimana, collassabile. Ogni settimana mostra: numero settimane · km totali · tipologia dominante (volume/intensità/recupero).

#### Sezione D — Adattamento AI
Card informativa: "Come il coach adatta il piano" — spiega che se il readiness è basso, le sessioni intense vengono proposte in giorni alternativi.

---

## 6. Componenti UI ricorrenti

### KPI Card
```
┌─────────────────────┐
│ LABEL PICCOLA    ↑  │  ← freccia trend colorata
│                     │
│     VALORE GRANDE   │
│     unità           │
└─────────────────────┘
```

### Insight Card
```
┌─── [colore bordo sinistro] ─────────────────┐
│  [icona]  Titolo breve                      │
│  Testo descrittivo con dato specifico.      │
└─────────────────────────────────────────────┘
```

### Gauge Readiness (hero element)
- Arco semicircolare (stile automotive / Whoop)
- Colore dell'arco: verde (#80+) → giallo (50-79) → rosso (<50)
- Numero al centro grande
- Etichetta sotto l'arco

### Gamification Strip
```
┌────────────────────────────────────────────────────────────┐
│  🔥 9 giorni   │  🥈 Dedicated  │ [████████░░] 3.240 XP  │
│                │  +760 → Elite  │                         │
└────────────────────────────────────────────────────────────┘
```

### Session Card (piano/suggerimento)
```
┌──────────────────────────────────────────────────────┐
│  🏃  Corsa Easy           40–50 min                  │
│  Zona 2  ·  < 148 bpm  ·  ~5:30/km                  │
│  ─────────────────────────────────────────────────   │
│  "Focus sul respiro. Nessun effort oggi."            │
└──────────────────────────────────────────────────────┘
```

---

## 7. Sidebar / Navigazione

**Desktop (fissa a sinistra, ~220px):**
```
● GarminCoach

[Coach]          ← active highlight
[Panoramica]
[Attività]
[Sonno]
[Salute]
[Performance]
[Corpo]
[Piano]

────────────────
● Garmin OK
↻ Sincronizza
Ultimo sync: 2h fa
```

**Mobile (bottom navigation, 5 tab):**
Coach · Panoramica · Attività · Salute · Piano

---

## 8. Palette colori dati (grafici)

| Variabile | Colore suggerito |
|-----------|-----------------|
| Passi / attività | Blue |
| FC / frequenza cardiaca | Rosso/corallo |
| Sleep score / sonno | Viola |
| VO2max / performance | Verde |
| Stress | Arancio |
| Body Battery | Cyan/teal |
| Training load | Ambra/giallo |
| Readiness | Verde lime |
| HRV | Indaco |

---

## 9. Flussi principali

### Flusso A — Mattina del runner
1. Utente apre `/coach`
2. Vede Readiness Score + messaggio del coach
3. Legge i 3 insight
4. Guarda l'allenamento suggerito
5. Va ad allenarsi

### Flusso B — Creazione piano
1. Utente va su `/plan`
2. Seleziona obiettivo (es. "10K")
3. Inserisce data gara target
4. L'AI genera il piano (loading 5–10 sec)
5. Vede il calendario settimana corrente
6. Ogni giorno vede la sessione + se il coach suggerisce adattamenti

### Flusso C — Analisi dati
1. Utente va su `/performance` o `/sleep`
2. Scorre i grafici temporali
3. Vede i record e le previsioni di gara
4. Torna su `/coach` per l'azione

### Flusso D — Sync dati
1. Bottone "Sincronizza" in sidebar o topbar
2. Toast "Sincronizzazione in corso…" con spinner
3. Toast "Sync completato — 14 attività aggiornate" (o errore se Garmin non raggiungibile)
4. Pagina si aggiorna automaticamente

---

## 10. Stato vuoto e onboarding

Se il DB è vuoto (primo accesso):
- **Banner prominente** su `/coach`: "Benvenuto! Premi Sincronizza per scaricare i tuoi dati Garmin."
- Nessuna pagina deve crashare — tutti i dati mancanti mostrano `—` o `N/D`
- Dopo il primo sync: redirect automatico a `/coach`

Se Garmin non configurato (no .env):
- Pagina di onboarding dedicata con istruzioni per il file `.env`

---

## 11. Dati esposti per pagina (riferimento tecnico)

| Pagina | Fonte principale | Aggiornamento |
|--------|-----------------|---------------|
| `/coach` | Tutti i modelli DB + AI cache giornaliera | Al sync + 1×/giorno |
| `/` | `daily_wellness` + `sleep_records` + `activities` | Al sync |
| `/activities` | `activities` | Al sync |
| `/sleep` | `sleep_records` | Al sync |
| `/health` | `daily_wellness` | Al sync |
| `/performance` | `training_metrics` + live Garmin | Al sync + live |
| `/body` | `body_composition` | Al sync |
| `/plan` | `training_plans` (DB) + AI generazione | On demand |

---

## 12. Out of scope (v1)

> **⚠️ Aggiornamento 2026-08-14:** multi-utente, notifiche (Web Push/email/Telegram), PWA e chat AI sono **entrati nello scope** con la v2 — vedi `docs/superpowers/specs/2026-08-14-garmin-coach-v2-multiuser-ai-design.md`.

- ~~Multi-utente / autenticazione~~ → in scope v2
- Integrazione mappe GPS (leaflet.js)
- ~~Notifiche push / email~~ → in scope v2
- Import da altre piattaforme (Strava, Polar)
- ~~PWA~~ → in scope v2 · app mobile nativa resta esclusa
- Inserimento manuale dati (pesi, idratazione)
