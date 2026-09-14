# Prompts e Ingegneria dei Contesti (AI)

Il "cervello" linguistico di Garmin Coach v2 è racchiuso nel modulo `app/ai/prompts.py`. 
In questo file sono definite in modo rigoroso tutte le istruzioni base (System Prompts) che vengono fornite ai modelli linguistici (Claude o OpenAI) a seconda del loro compito.

La filosofia generale applicata in tutti i prompt è: **niente allucinazioni, niente frasi fatte, massima concisione e attinenza rigorosa ai dati misurati.**

Di seguito la descrizione dei vari agenti e delle loro istruzioni.

## 1. Daily Coach (Il messaggio del mattino)
È il messaggio visibile nella home `/coach`.
- **System Prompt:** Istruisce il modello ad agire come un allenatore personale. Il tono richiesto è tecnico, diretto e conciso ("solo logica e dati, niente emoji, niente motivazione generica").
- **Regole imposte:** Il messaggio deve essere di massimo 3 frasi, deve citare esplicitamente un dato numerico del giorno per dimostrare di averlo letto e deve concludersi con l'indicazione pratica su cosa fare oggi.
- **Inibizioni:** Gli viene intimato di *non* contraddire il punteggio di prontezza e l'allenamento suggerito (che sono calcolati dal motore Python), ma solo di "spiegarli".

## 2. Chat Agent (Conversazione libera)
È l'agente che interagisce in `/chat` e che ha a disposizione i Tool per interrogare il DB.
- **System Prompt:** Condivide con il Daily Coach le regole di base (tono e sicurezza), ma ha un prompt molto più esteso perché gestisce l'intera dinamica di allenamento.
- **Regole imposte (Read The Series):** Viene istruito a capire la differenza tra "una giornata storta" (che non fa statistica) e un "trend negativo".
- **Regole di Sicurezza (Safety):** Deve impedire aumenti di volume superiori al 10% settimanale, mai suggerire due giorni di qualità consecutivi e mandare dal medico in caso di sintomi persistenti.
- **Output Strutturato:** Se decide di proporre un allenamento specifico durante la chat, deve generare in parallelo un blocco JSON (chiamato *scheda allenamento*) in modo che la UI possa renderizzarlo graficamente bene anziché produrre un "muro di testo".

## 3. Training Plan Generator (Creazione dei Piani)
È il modello chiamato on-demand quando si genera un nuovo piano su più settimane.
- **System Prompt:** Viene istruito a comportarsi da allenatore specializzato nella programmazione a medio/lungo termine. 
- **Logica (Race Phases):** Il modello riceve delle "linee guida" su come dividere le settimane a seconda della distanza dalla gara (Costruzione, Picco, Scarico, ecc.).
- **Vincolo output:** Il prompt gli intima di generare l'intero output esclusivamente in formato JSON strutturato (senza alcun testo colloquiale attorno) per permettere al server di salvarlo direttamente a database e mostrarlo nel calendario.

## 4. Insight Generator (Analisi Trend)
Un agente "silenzioso" che genera i 3 snippet di insight giornalieri.
- **System Prompt:** È programmato per fare il lavoro dell'analista dati. Gli viene esplicitamente proibito di ripetere ciò che l'utente già vede ("il tuo sonno è 40").
- **Regole imposte:** Un insight *deve* mettere in relazione due elementi (es. il sonno con il carico del giorno prima). Deve concludersi in modo *azionabile*, ad esempio "Fai un lungo leggero" e non con frasi inutili come "Fai attenzione al riposo" o "Ascolta il tuo corpo".

## 5. Page Readings (Didascalie delle pagine metriche)
L'agente che genera le piccole didascalie testuali per le dashboard storiche (Sonno, Performance, Corpo).
- **System Prompt:** L'IA riceve un "verdetto" dal codice Python (es. Verde, Giallo o Rosso). Il prompt vieta categoricamente all'IA di cambiare il tono del verdetto: se il codice dice che la situazione è critica, l'IA non può rassicurare l'utente. Deve limitarsi a trasformare la matematica in linguaggio naturale con una frase di titolo e un'azione da fare.
