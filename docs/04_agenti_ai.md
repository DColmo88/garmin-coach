# Agenti di Intelligenza Artificiale

L'uso dell'Intelligenza Artificiale in Garmin Coach è attentamente limitato ai contesti dove il "linguaggio verbale" e l'"adattamento flessibile" eccellono (motivare l'utente o creare un piano di allenamento flessibile). Tutto il resto (grafici, numeri, valutazioni tecniche) è gestito deterministicamente dal codice Python, per contenere i costi delle API e azzerare i tempi di latenza.

L'interazione con le API dei provider (es. Claude o OpenAI) avviene per i seguenti due motivi principali:

## 1. Daily Coaching (Il Coach del Mattino)

La pagina `/coach` accoglie l'utente con un messaggio testuale da parte del suo allenatore virtuale.

- **Frequenza della chiamata:** 1 volta al giorno per utente.
- **Cache:** Il messaggio generato viene salvato nel database (`DailyCoachCache`). L'AI viene richiamata solo allo scattare del giorno successivo o in caso di sync forzata profonda, non ad ogni refresh della pagina `/coach`.
- **Modello Consigliato:** Modello "light" e veloce (es. Claude 3 Haiku o GPT-4o-mini).
- **Contesto Fornito (Prompt):** Il system prompt non invia dati crudi. Invia un minuscolo payload aggregato, calcolato precedentemente dal motore deterministico. Questo payload (`context.py`) include:
  - Il Readiness Score di oggi (es. "78/100").
  - Lo stato del sonno di stanotte.
  - La tendenza della FC a riposo.
  - L'ultimo allenamento completato.
  - L'obiettivo impostato dall'utente (es. "Preparare 10K sotto i 45m").
- **Output dell'AI:** Un messaggio motivazionale (2-3 frasi) e il suggerimento testuale per l'allenamento di oggi (tipo, durata, zona e tattica psicologica).

## 2. Generazione del Training Plan

L'utente, dalla pagina `/plan`, può richiedere la generazione di un programma di allenamento personalizzato per un obiettivo (es. Maratona, Recupero, Forma Generale).

- **Frequenza della chiamata:** Generato "on-demand" su input utente, ma molto raramente (massimo poche volte al mese).
- **Modello Consigliato:** Modello "heavy", capace di ragionamenti complessi (es. Claude 3.5 Sonnet o GPT-4o).
- **Contesto Fornito:** Dati storici aggregati dell'ultimo mese (es. volume in KM), il VO2Max calcolato, il profilo fisiologico e la data target della gara.
- **Output dell'AI:** L'AI non risponde in prosa testuale libera, ma restituisce un **JSON Strutturato** per tutte le settimane del piano.
- **Adattamento Quotidiano:** Il piano json generato viene salvato sul DB. Ogni mattina, il sistema *deterministico* controlla il piano generato. Se la "Prontezza" (Readiness) dell'utente è insufficiente, il codice (non l'AI) suggerirà di spostare l'intervallo intenso al giorno successivo.

## 3. Tool Calling & Chat on-demand (v2)

Con la versione 2, è presente la chat AI. In questa area l'utente conversa liberamente col coach.
Per evitare allucinazioni ed evitare l'invio "a pioggia" dell'intero DB storico nel prompt ad ogni messaggio, la Chat usa un paradigma **Agentico** con **Tool Calling**.

- L'AI ha un system prompt limitato al riassunto del giorno (lo stesso del Daily Coaching).
- **Se** la domanda dell'utente tocca periodi storici, argomenti complessi o il suo **Piano di Allenamento**, il modello ha a disposizione un set di "tools" in `tools.py` (funzioni in Python che l'agente può eseguire).
- I Tools eseguono query specifiche al DB, aggregando le metriche richieste nel range di tempo opportuno, oppure restituiscono la settimana in corso e successiva del piano di allenamento attivo (`tool_get_training_plan`).
- Il modello legge il ritorno del tool e formula la risposta verbale all'utente.

Questo sistema blocca alla radice gli sprechi di token e i timeout di connessione garantendo al tempo stesso un'esperienza di chat integrata, in cui l'agente conosce non solo il passato (i dati), ma anche il futuro (il piano programmato).
