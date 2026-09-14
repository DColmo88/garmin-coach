# Logica delle Metriche

Garmin Coach poggia su un solido motore deterministico per calcolare le metriche fisiche e sportive, limitando l'uso dell'Intelligenza Artificiale alla sola interpretazione narrativa.
Questa separazione garantisce numeri prevedibili, precisi, testabili e generabili a costo zero (non ci sono token API necessari per calcolare i KPI).

Tutti questi calcoli si trovano in `app/analysis/` e `app/ai/readiness.py`.

## 1. Readiness Score (Prontezza)

Il punteggio composito da 0 a 100 che indica la prontezza quotidiana all'allenamento. Viene calcolato integrando diverse sorgenti di dati fornite da Garmin. Se una fonte manca, i pesi si ridistribuiscono tra i dati presenti, anziché azzerare il punteggio o inserire valori neutri casuali.

La formula base dei pesi (in caso tutti i dati siano presenti) è:
- **Sonno (Sleep Score):** 30% (valore base fornito da Garmin da 0 a 100)
- **HRV (Heart Rate Variability):** 25% (balanced=100, unbalanced=40, missing=65)
- **Body Battery Max:** 20% (valore da 0 a 100, prende il max notturno)
- **Carico (ACWR / Load Ratio):** 15% (equilibrio tra carico acuto e cronico, ottimale a 80, overtraining a 30)
- **Trend FC a riposo:** 10% (trend in discesa/stabile = forma buona, trend in forte ascesa = affaticamento/malattia)

**Classi di output visivo per l'utente:**
- `80-100`: 🔥 **Pronto** - Allenamento intenso / sessione chiave consigliata.
- `65-79`: ✅ **Buono** - Allenamento moderato.
- `50-64`: 🟡 **Discreto** - Corsa easy / attività leggera.
- `35-49`: 🔵 **Stanco** - Recovery run o riposo attivo.
- `0-34`: ❌ **Riposo** - Riposo completo consigliato.

## 2. Carico di Allenamento e Fisiologia

Spesso le metriche fornite nativamente da Garmin ("Training Load") presentano buchi nei dati se l'utente non indossa l'orologio tutto il tempo o usa dispositivi di fasce diverse. Per questo motivo, l'app *ricalcola* i carichi in autonomia, a partire dalla durata e della frequenza cardiaca delle attività:

- **Soglie:** Vengono osservate dai dati storici o stimate, se l'utente non le dichiara esplicitamente in `/settings` (FC Max, FC a riposo, Soglia LTHR).
- **TRIMP (Training Impulse):** Formula di Banister per il carico cardiovascolare per sessione.
- **ACWR (Acute:Chronic Workload Ratio):** Rapporto tra l'affaticamento acuto (ATL, 7 giorni) e il livello di forma cronico (CTL, 42 giorni). Il valore non viene calcolato a meno che l'utente non abbia uno storico sufficiente (almeno 8 giorni allenanti su 28), in modo da non generare falsi allarmi di overtraining.
- **Distribuzione FC (Zone):** Garmin calcola i secondi in ciascuna zona. Il sistema mappa i tempi nelle famose 5 zone e riconosce la "zona grigia" (quando ci si allena troppo forte per il recupero, ma troppo piano per il guadagno).

## 3. Motore degli Insights (Regole)

Gli *Insight* (le pillole informative sotto la prontezza nella homepage) sono generati da regole e trigger deterministici su medie mobili a 7 e 30 giorni.

Esempi di regole hardcoded:
- `Resting HR`: Se la FC media a 7gg è scesa di almeno 3 battiti rispetto a 30gg fa → "Fitness in crescita".
- `Carico / ACWR`: Se ACWR supera 1.4 → "Rischio Overtraining".
- `Sonno`: Se lo sleep score a 7gg ha una media bassa → "Priorità al recupero notturno".
- `Monotonia`: Se si nota lo stesso stimolo ripetuto costantemente e senza variazioni o riposi veri.

*(L'engine ne identifica molti, li ordina per priorità, e l'interfaccia ne mostra solo i primi 3 più rilevanti).*

## 4. Gamification (XP, Livelli, Streak)

La Gamification viene interamente calcolata sui dati storici ad ogni sincronizzazione (non vi è input utente manuale) in `app/gamification.py`.

- **XP (Punti Esperienza):** Si guadagnano allenandosi (base 50 + extra a km), per aver dormito bene (score > 80), allenamenti consecutivi, ecc.
- **Livelli:** Avanzamento in gradi: *Beginner, Athlete, Dedicated, Elite, Legend*.
- **Streak:** Conteggio ininterrotto di "giorni attivi" (almeno un'attività), "notti recuperate" o settimane con carico bilanciato.
- **Badge:** Traguardi (10K, prime Speed run, costanza nel recupero).

Nessun calcolo AI interviene qui: è puramente deterministico.
