# Funzionamento Generale (Overview)

Garmin Coach (v2) è un'applicazione web multi-utente che funge da cruscotto e coach personale intelligente. Si basa sui dati raccolti dai dispositivi Garmin e aggiunge un livello di intelligenza interpretativa (AI e regole deterministiche) per fornire raccomandazioni giornaliere.

## 1. Cosa fa l'applicazione?

L'obiettivo principale di Garmin Coach è rispondere in 5 secondi alla domanda del mattino dell'atleta: **"Come sto oggi e cosa devo fare?"**

L'utente, entrando nella landing page `/coach`, vede immediatamente:
- Un punteggio di prontezza (**Readiness Score**).
- 3 **Insight** testuali sulle sue condizioni e i trend (es. "FC riposo in calo, stai recuperando bene").
- Un **messaggio personalizzato** dal coach AI.
- L'**allenamento suggerito** per la giornata.

## 2. Il Flusso dei Dati (Sync Giornaliera)

L'applicazione non poggia su webhook in tempo reale da Garmin, ma adotta una strategia a **sincronizzazione programmata**:
1. Ogni notte/mattina presto, lo scheduler avvia un ciclo per ogni utente registrato.
2. Vengono chiamate le API non ufficiali di Garmin per prelevare il carico di allenamento, il sonno, lo stress, le attività recenti (con i minuti passati nelle varie zone di frequenza cardiaca) e la Body Battery.
3. Questi dati vengono inseriti/aggiornati in formato time-series all'interno del database (Postgres o SQLite).
4. Subito dopo, la pipeline interna calcola le nuove metriche composite, come il Readiness Score, aggiorna lo stato della Gamification (livelli e XP), e deduce le regole degli insights.

## 3. L'Intervento dell'Intelligenza Artificiale (AI)

Un principio guida dell'architettura è: **il determinismo fa i numeri, l'AI fa le parole**.
Il sistema calcola tutto tramite formule matematiche in Python puro per avere costi pari a zero e nessuna latenza:
- Il Readiness Score (0-100)
- Il calcolo dell'ACWR (carico acuto vs cronico)
- Le ore trascorse nelle zone FC.

Una volta preparati i numeri, un motore AI (come Claude o OpenAI) legge questi riassunti pre-calcolati (senza mai vedere le serie di dati raw, per contenere i costi e velocizzare i prompt) ed elabora **due output principali**:
- **Coaching Narrativo:** Un breve incoraggiamento mattutino (cachato una volta al giorno).
- **Training Plan:** La generazione del piano d'allenamento per obiettivi di lungo periodo.

*(Vedi il documento dedicato all'AI `04_agenti_ai.md` per i dettagli tecnici sulle chiamate).*

## 4. Architettura dell'Informazione

L'app si suddivide in sezioni chiave:
- **Coach (`/coach`)**: La homepage motivazionale del giorno, contenente Readiness e allenamento odierno.
- **Panoramica (`/`)**: I KPI crudi e oggettivi, le attività storiche recenti e i grafici principali.
- **Obiettivi & Piano (`/goals` e `/plan`)**: Dove si fissa il traguardo (es. una Maratona o un generico stato di forma) e si consulta il calendario generato dal Coach.
- **Sezioni Salute (Attività, Sonno, Performance)**: Viste di esplorazione profonda delle time-series tramite grafici Chart.js.

## 5. Notifiche

L'app è in grado di inviare notifiche proattive tramite tre canali distinti:
- Web Push (in quanto l'app è installabile come PWA)
- Email (via SMTP)
- Telegram (tramite un bot)

L'utente sceglie quali notifiche ricevere. Di default il sistema invia solo gli avvisi importanti (come il rischio di sovrallenamento o un record personale), calcolati nella pipeline successiva alla sync notturna.
