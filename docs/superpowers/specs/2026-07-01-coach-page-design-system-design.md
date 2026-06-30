# Design — Pagina Coach + Design System (blend Oura/Whoop)

**Data:** 2026-07-01
**Stato:** approvato dall'utente, pronto per il piano di implementazione

---

## 1. Contesto e obiettivo

Il Garmin Connector è oggi una dashboard di sola lettura (overview, activities, sleep,
health, body, performance, devices, ai) con styling chiaro/base. Esistono già due
documenti di prodotto — `PRD_GarminCoach.md` e `AI_COACHING_DESIGN.md` — che descrivono
la visione di un "coach personale". Nessuna parte di quella visione è ancora implementata.

Gli screenshot di riferimento forniti dall'utente (cartella `inspo_interface/`) sono di
**Oura** (singolo grande ring + narrativa) e **Whoop** (più ring affiancati + card
"Monitor" dense). Il PRD esistente cita già queste app come mood di riferimento.

**Obiettivo di questo ciclo:** costruire la nuova landing `/coach` in stile **blend
Oura/Whoop** e un **design system dark riutilizzabile** (token CSS + componenti) che le
altre pagine potranno adottare in cicli successivi.

### Scelte confermate con l'utente
- **Scope:** Coach + design system (NON tutto il PRD, NON reskin delle altre pagine).
- **Look:** blend — hero singolo ring (Oura) + mini-ring e card Monitor (Whoop).
- **AI:** messaggio coach **deterministico** (template Python sui dati reali), con seam
  `coach()` sul provider per agganciare un vero `ClaudeProvider` in futuro. Nessuna chiave
  API è configurata e il design doc raccomanda l'approccio deterministico-first.

### Fuori scope (questo ciclo)
- Gamification (XP, streak, badge), pagina `/plan`, chiamata AI reale.
- Reskin delle 7 pagine esistenti (verranno migrate al design system in seguito).
- Sync intraday (serie minuto-per-minuto): il DB conserva solo riepiloghi giornalieri,
  quindi le curve real-time degli screenshot non sono realizzabili ora.

### Mappa fattibilità dai dati (riferimento)
- **Realizzabile** (dati presenti): ring Readiness, ring Sonno, card HRV, card RHR,
  Stress Monitor, calorie/burn, media passi, Body Battery, VO₂max, insight narrativi
  deterministici, barre progresso abitudini, "X/5 metriche in range".
- **Non realizzabile / fuori scope:** Whoop Strain & Whoop Age/Healthspan (algoritmi
  proprietari, nessun dato), Community/Teams (app single-user), Advanced Labs (no esami
  del sangue), Oura Cycle Day (dato ciclo non applicabile), curve intraday (solo
  riepiloghi giornalieri nel DB), pulsanti "Start activity / Set alarm" (dashboard di
  sola lettura).

---

## 2. Design system (CSS token + componenti)

Estensione di `app/static/style.css` (nessun framework aggiunto; resta Jinja2 + JS vanilla).

### Token
- Sfondo `--bg:#0c0e14`, superfici `--surface:#161922`, `--surface-2:#1d212c`.
- Bordi hairline, raggi morbidi 16–20px, ombre soft.
- Accent primario **teal `#00d4aa`**; **amber `#f5a623`** per il gradiente readiness.
- Bande punteggio: verde `≥80`, amber `50–79`, rosso `<50`.
- Tipografia: Inter/system sans; numeri display grandi per gli score.

### Componenti riutilizzabili (classi CSS, usabili da ogni pagina in futuro)
- `.ring` — gauge circolare in **SVG inline** via macro Jinja, colore in base alla banda.
  Niente Chart.js per i ring.
- `.metric-card`, `.monitor-card` — card numero/metrica densa.
- `.insight-card` — bordo sinistro colorato (verde/amber/rosso) + icona + titolo + testo.
- `.coach-hero` — blocco hero con gradiente soft.
- `.pill` — pill breakdown (label + valore + colore).
- `.progress-bar` — barra lineare per progressi/abitudini.

### Macro Jinja per i ring
Una macro `ring(value, max, label, sublabel)` in un partial template
(`templates/_components.html`) che produce l'SVG del cerchio progress. Riutilizzabile per
hero e mini-ring. Gestisce `value=None` mostrando "—".

---

## 3. Readiness engine — `app/ai/readiness.py` (Python puro, deterministico)

Formula dal design doc:

```
readiness = sleep_score        × 0.30
          + hrv_factor          × 0.25
          + body_battery_max    × 0.20
          + load_ratio_factor   × 0.15
          + resting_hr_factor   × 0.10
```

Dettaglio fattori:
- `sleep_score`: 0–100 da `sleep_records.sleep_score`.
- `hrv_factor`: `balanced→100`, `unbalanced/low→40`, mancante→`65`
  (da `training_metrics.hrv_status`).
- `body_battery_max`: 0–100 da `daily_wellness.body_battery_high`.
- `load_ratio_factor`: rapporto carico acuto(7g)/cronico(28g);
  `0.8–1.2→80`, `1.2–1.5→55`, `>1.5→30`, `<0.8→70`, mancante→`65`.
- `resting_hr_factor`: trend RHR 7g vs 30g; in calo→`80`, stabile→`65`, in salita→`40`,
  mancante→`65`.

**Output** (dataclass/dict):
- `score: int` (0–100, arrotondato).
- `label: str` + `emoji` su 5 bande:
  - 80–100 🔥 Pronto → "Allenamento intenso / sessione chiave"
  - 65–79 ✅ Buono → "Allenamento moderato"
  - 50–64 🟡 Discreto → "Corsa easy / attività leggera"
  - 35–49 🔵 Stanco → "Recovery run o riposo attivo"
  - 0–34 ❌ Riposo → "Riposo completo"
- `breakdown: list` dei 5 contribuenti, ognuno `{name, value, color}` per le pill.

**Robustezza:** ogni fattore mancante usa un default neutro; lo score si calcola sempre.
Se TUTTI i segnali principali mancano → `score=None` e il page mostra stato vuoto.

---

## 4. Insights engine — `app/ai/insights.py` (Python puro)

~12 regole dal design doc, ciascuna ritorna `{icon, title, text, color}` con numeri reali:
- RHR 7g < RHR 30g − 3 → "FC riposo −X bpm vs mese scorso → fitness in crescita" (verde)
- sleep_score medio 7g < 65 → "Sonno scarso questa settimana → priorità recupero" (amber)
- carico acuto > cronico × 1.4 → "Carico acuto alto — rischio overtraining" (rosso)
- vo2max ultimo > vo2max 4 settimane fa → "VO₂max +X.X → più forte di un mese fa" (verde)
- giorni dall'ultima corsa > 5 → "X giorni senza corsa — corpo riposato" (verde)
- giorni attivi consecutivi ≥ 7 → "🔥 X giorni consecutivi attivi" (verde)
- sonno profondo % < 15 → "Sonno profondo basso — migliora rituale serale" (amber)
- body_battery_low medio < 20 → "Body Battery quasi a zero ogni sera — stai facendo troppo" (amber)
- (altre regole minori: stress alto, steps sotto goal trend, ecc.)

La pagina mostra i **top 3** per rilevanza (priorità: rosso > amber > verde, poi ordine
di definizione). Generazione in Python puro: zero latenza, zero costo.

---

## 5. Messaggio coach — `app/ai/coaching.py` (template deterministici) + seam provider

- Costruisce la narrativa di 2–3 frasi riempiendo template con i dati reali e la banda di
  readiness (es. "Hai dormito 7h12 con score 78. HRV stabile e Body Battery a 91 — sei in
  forma. Mantieni il ritmo.").
- Costruisce l'**allenamento suggerito** in base alla banda readiness:
  `{type, icon, duration, hr_zone, note}`.
- **Seam provider:** aggiunge `coach(context) -> CoachOutput` all'interfaccia
  `AIProvider` (`app/ai/base.py`). Implementazione default in `coaching.py`/StubProvider =
  template. Un futuro `ClaudeProvider` farà override con chiamata reale. **Nessuna chiamata
  di rete in questo ciclo.**

---

## 6. Pagina `/coach` — layout (blend)

Sezioni dall'alto:
1. **Hero (Oura):** ring grande Readiness + score + label stato + messaggio coach +
   5 pill breakdown.
2. **Riga mini-ring (Whoop):** Sleep score · Body Battery · VO₂max (o RHR) come ring piccoli.
3. **Card Monitor (Whoop):** "Health Monitor — X/5 metriche in range" + "Stress Monitor".
4. **Insight cards:** top 3, bordi colorati.
5. **Card allenamento suggerito:** tipo · durata · zona FC · nota tattica.

### Routing
- Nuova route `GET /coach` in `app/routers/pages.py`, render di `coach.html`.
- `/coach` diventa la landing principale; `/` (overview) resta raggiungibile dalla nav.
- Link "Coach" aggiunto alla nav in `base.html` (prima voce, evidenziata se attiva).

---

## 7. Accesso dati — `app/queries.py` esteso

Helper read-only, None-safe:
- ultimo valore + finestre 7g/30g per ogni metrica rilevante;
- rapporto carico acuto/cronico (somma training_load 7g vs media 28g);
- giorni dall'ultima attività; giorni attivi consecutivi;
- conteggio "metriche in range" per Health Monitor.

Tutte difensive: campi mancanti → `None`, nessuna eccezione su DB sparso.

---

## 8. Stati vuoti ed errori

- DB vuoto / metrica mancante: i ring mostrano "—"; il messaggio coach diventa
  "Premi Sincronizza per scaricare i tuoi dati Garmin."
- La pagina non restituisce mai 500: ogni accesso a dati è protetto.

---

## 9. Testing

- `pytest` unit test per `readiness.py`: confini delle bande, default su dati mancanti,
  calcolo breakdown.
- `pytest` unit test per `insights.py`: ogni regola scatta su fixture costruite ad hoc;
  selezione top-3 corretta.
- Verifica visiva manuale contro il DB locale reale (`data/garmin_connector.db`, già
  popolato) avviando uvicorn dal venv.
- `pytest` aggiunto a `requirements.txt`.

---

## 10. Riepilogo file

**Aggiunti:**
- `app/ai/readiness.py`
- `app/ai/insights.py`
- `app/ai/coaching.py`
- `app/templates/coach.html`
- `app/templates/_components.html` (macro ring/card)
- `tests/test_readiness.py`, `tests/test_insights.py`

**Modificati:**
- `app/static/style.css` (token + componenti dark)
- `app/queries.py` (helper trend/finestre)
- `app/routers/pages.py` (route `/coach`)
- `app/ai/base.py` + `app/ai/provider.py` (seam `coach()`)
- `app/templates/base.html` (link nav Coach)
- `requirements.txt` (pytest)
