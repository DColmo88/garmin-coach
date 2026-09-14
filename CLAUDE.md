# Garmin Coach

Coach personale sui dati di **Garmin Connect**: dashboard interpretate, chat con
un allenatore AI che conosce i tuoi dati, obiettivi configurabili, piani di
allenamento e notifiche.

**Stato: completo e in produzione** su https://garmin.46.224.17.241.sslip.io
869 test verdi.

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
- il system prompt della chat contiene un **briefing** già calcolato
  (`app/ai/briefing.py`): serie di 14 giorni, confronto col normale della
  persona, ultimi 8 allenamenti. Circa 600 token invece di 150 — è una spesa
  voluta, vedi sotto;
- per i dati storici il modello chiama i tool in `app/ai/tools.py`, che aggregano
  da soli (oltre 35 giorni → medie settimanali, oltre 180 → mensili);
- coaching, insight e letture delle pagine condividono **lo stesso briefing**,
  costruito una volta, e sono cachati per giorno: tre chiamate al giorno per
  utente in tutto, non tre per pageview (~0,30 $/mese a persona);
- insight e letture usano il modello **grande**, e non è spreco: su un confronto
  settimana-su-settimana `gpt-4o-mini` ha letto «in calo» un valore che saliva.
  Un insight sbagliato è peggio di nessun insight;
- quote per utente, con consumo e spesa stimata visibili in `/admin`.

Prima di aggiungere una feature, chiediti se il calcolo può stare in Python.
Se sì, ci sta.

Da qui viene anche la moneta della gamification: gli XP seguono
`activity_load`, non i chilometri. Premiare il volume grezzo in un'app che
passa metà del proprio codice a spiegare che i chilometri non descrivono
l'allenamento — TRIMP, zone, la zona 3 «che costa quanto una seduta dura senza
darne lo stimolo» — era una contraddizione visibile: cento chilometri di bici
valevano dieci volte un fartlek.

**Attenzione a dove passa il confine.** «Il determinismo fa i numeri» non vuol
dire che i *testi* debbano essere fissi. Insight del giorno e verdetti delle
pagine erano quarantuno frasi scritte a mano scelte da soglie: funzionavano,
ma dopo una settimana le avevi lette tutte e nessuna metteva mai in relazione
due cose — «sonno sotto la media» è un numero riletto ad alta voce.

Adesso li scrive l'AI, e il confine è questo:

| Chi | Cosa |
|---|---|
| Python | soglie, tono (verde/ambra/rosso), medie, delta, correlazioni |
| AI | le parole, e la scelta di cosa vale la pena dire |

L'AI non può cambiare un tono: se le soglie dicono rosso, può solo dirlo
meglio. E i numeri non li calcola — arrivano già fatti nel blocco
«CONFRONTI GIÀ CALCOLATI» del briefing — quindi può scegliere male cosa dire,
non sbagliare una cifra.

## Cosa vede davvero il modello

Il briefing (`app/ai/briefing.py`) è il punto in cui si decide se il coach
capisce l'atleta o no. Prima mandava una manciata di valori puntuali, e con
«sonno 41» davanti qualunque modello scrive «riposa» — anche se le sei notti
prima erano fra 78 e 84.

Tre cose che adesso ci sono:

1. **Le serie, non i punti.** Quattordici giorni in una riga sola: il modello
   vede la forma dell'andamento, non l'ultimo campione. **Una colonna per
   giorno di calendario**, buchi compresi: prima le righe venivano da elenchi
   di misurazioni esistenti, quindi con tre notti non registrate la riga
   «Sonno» copriva diciassette giorni in quattordici celle mentre «Carico» —
   costruita per data — ne copriva quattordici. Il prompt dice di leggerle
   incolonnate, e non lo erano.
2. **Il confronto col proprio normale.** Non «sonno 41», ma «sonno 41, sotto la
   norma, tipico 72-85, **fuori norma da 1 giorno**». Quel «da 1 giorno»
   distingue una notte storta da una settimana storta, e si conta sui giorni di
   calendario: contarlo sulle misurazioni lo gonfiava a ogni notte non
   registrata. E «oggi» è oggi — se il valore più recente è di due giorni fa lo
   dice, invece di spacciarlo per stanotte.
3. **Gli allenamenti, sempre.** Prima il modello doveva chiederli con un tool, e
   spesso rispondeva senza.
4. **Come dice di stare**, e **se sta seguendo il piano**. Il check-in del
   mattino e l'aderenza (sedute previste contro eseguite): il primo batte i
   sensori, la seconda prima non esisteva affatto — il coach consigliava la
   giornata senza sapere che l'atleta aveva saltato tre sedute su quattro.

Il prompt porta la regola corrispondente: *una giornata storta non è un trend*.
Il riposo si consiglia quando più segnali convergono per più giorni, o quando
c'è un carico che lo giustifica.

Quando il coach propone una seduta emette anche un blocco delimitato da
`​```allenamento` con dentro il JSON: `app/ai/workout_card.py` lo trasforma in
una scheda con icona per tipo, tre fasi e razionale. Se il JSON è rotto resta il
testo — non è mai un errore fatale.

I prompt (fasi di preparazione, calibrazione dei ritmi, limiti di sicurezza)
vengono dal progetto `AI Coach` precedente, dove erano già più densi di questi.

## Il carico se lo calcola l'app

Garmin, per questo account, non popola `training_load`: 28 giorni di metriche,
zero valori. «Carico acuto», «rischio di sovrallenamento» e il 15% del punteggio
di prontezza poggiavano su un campo vuoto e restituivano sempre lo stesso valore
neutro.

`app/analysis/` ricalcola tutto dai dati che Garmin dà davvero — durata,
frequenza cardiaca, potenza — con le formule della letteratura: TRIMP di
Banister, curve CTL/ATL/TSB del Performance Management Chart, rapporto
acuto/cronico, monotonia di Foster. Le soglie fisiologiche stanno in
`/settings`; se mancano vengono osservate dai dati o stimate, e ogni pagina
dichiara quale delle tre cose è successa.

**Le formule, nella versione corretta.** Tre cose sistemate:

- il **rapporto acuto/cronico è scollegato**: la settimana acuta non sta dentro
  il proprio denominatore. Nella versione accoppiata, raddoppiare il carico
  alzava anche il riferimento, e una salita netta usciva più mite di quello che
  era (la critica di Lolli e altri alle correlazioni spurie);
- la **monotonia satura invece di sparire**: con sette giorni di carico
  identico la variabilità è zero e prima si restituiva `None`, cioè la
  settimana più monotona possibile era l'unica su cui l'app non diceva niente —
  proprio il caso che la metrica esiste per segnalare;
- il **seme delle curve non guarda avanti**: si stima sulle prime quattro
  settimane della finestra e non sulla media di centottanta giorni, dove un
  blocco di lavoro di agosto decideva da dove partiva la curva di marzo.

**Due regole di onestà, entrambe emerse dai dati veri:**

- il rapporto acuto/cronico non si calcola sotto gli otto giorni di allenamento
  in quattro settimane — chi esce una volta ogni tre settimane otterrebbe 3,0 e
  un allarme sovrallenamento per una singola uscita, perché il denominatore è
  quasi zero;
- un fattore di prontezza senza dato **non vale 65, non vale niente**: i pesi si
  ridistribuiscono su quelli misurati e l'interfaccia mostra il fattore spento.

Il tempo per zona FC arriva da un endpoint separato (`get_activity_hr_in_timezones`),
una chiamata per attività, al massimo 25 per sync. Da quella stessa risposta si
prendono anche **i battiti in cui ogni zona comincia**: l'app li ricalcolava per
conto suo come percentuali della FC massima, mentre l'orologio può avere le zone
tarate su riserva cardiaca o su soglia — il grafico contava con una scala e la
legenda ne dichiarava un'altra. Quando i confini dell'orologio non ci sono si
ricade sulla derivazione, e la pagina lo dice.

## Quello che nessun sensore sa

L'unico dato dell'app che non arriva da un apparecchio, e in letteratura quello
che predice l'affaticamento meglio di HRV e frequenza a riposo.

| Cosa | Dove | Scala |
|---|---|---|
| Check-in del mattino | in cima a `/coach`, solo per oggi | energia, gambe, testa, sonno percepito — 1-5, sempre «più alto è meglio» |
| Sforzo percepito (RPE) | su `/coach` per le sedute degli ultimi 3 giorni, e sul dettaglio attività | 1-10, Borg CR10 |

Non è un diario: **entra nei conti.**

- l'RPE diventa carico (sRPE di Foster: sforzo × minuti) in `activity_load`,
  subito dopo potenza e frequenza cardiaca e ben prima della durata. È l'unica
  misura del carico che esista per chi non indossa una fascia, e sulle ripetute
  corte è più onesta della FC media, che arriva in ritardo sullo sforzo;
- il check-in è un fattore di prontezza da 0,15;
- entrambi finiscono nel briefing, e il prompt dice al modello che **quello che
  l'atleta dice batte quello che dicono i sensori**: un orologio non sa che ha
  dormito in treno.

Nessun campo è obbligatorio e il check-in si sovrascrive: chi risponde a una
domanda su quattro dà comunque un'informazione, e alle sette di mattina uno può
sbagliare uno slider.

**Conseguenza sulle capacità dei fornitori.** Chi usa Strava non ha sonno, HRV
né Body Battery, e la prontezza per lui era impossibile per sempre. Adesso
soggettivo + carico + forma superano la soglia di fondatezza: la prontezza
compare per chi ha fatto **almeno tre check-in negli ultimi sette giorni**
(`providers.readiness_available`). La regola resta «quello che un utente non
può avere non esiste» — è cambiato cosa può avere.

## Il punteggio di prontezza

Sette fattori invece di cinque, e tre regole.

| | Peso |
|---|---|
| Sonno | 0,22 |
| HRV | 0,18 |
| Body Battery | 0,15 |
| Come stai (check-in) | 0,15 |
| Carico (acuto/cronico) | 0,12 |
| Forma (TSB) | 0,10 |
| FC riposo | 0,08 |

**Un fattore senza dato non vale 65, non vale niente.** I pesi si
ridistribuiscono su quelli misurati (regola di sempre).

**Un dato di ieri vale meno, ma vale.** Il peso decade con l'età — 1,0 oggi,
0,6 ieri, 0,3 l'altroieri, poi zero. Prima il valore contava solo se la riga era
esattamente di oggi: chi apriva l'app prima della sync delle 6:30, o dopo una
notte in cui era fallita, leggeva «Dati insufficienti» con due mesi di storia
alle spalle. Un fattore vecchio dichiara la propria età in interfaccia.

**Un punteggio si legge contro la propria storia.** Con almeno dieci giorni di
misurazioni ogni fattore diventa il proprio rango percentile su sessanta giorni
(`app/analysis/baseline.py`). Il punteggio del sonno di Garmin è già una media
di popolazione: 72 per chi dorme abitualmente 85 è una brutta notte, per chi sta
sui 60 è la migliore del mese, e prima contribuivano identici. Sotto i dieci
giorni si torna alla lettura assoluta.

Restano assoluti **carico e forma**: sono già normalizzati sulla persona per
costruzione, e le loro soglie vengono dalla letteratura sugli infortuni, non
dalla distribuzione di questo atleta.

## Il fuso orario è quello dell'atleta

`User.timezone` esisteva dalla v2 e non lo leggeva nessuno: tutto passava da
`date.today()`, cioè dal fuso del server. Su quel «giorno» poggiano la chiave
della cache del coaching, l'azzeramento delle quote AI, le serie del briefing,
il cooldown delle notifiche, la finestra del rapporto acuto/cronico e la serie
della gamification. Con il server su UTC e l'atleta a Roma, per due ore ogni
notte l'app parlava di ieri.

Adesso passa tutto da `app/clock.py` (`today_for(user)`, `naive_utc()`), e il
fuso non compare da nessun'altra parte.

Una cosa che **non** si converte: `Activity.start_time`. Entrambi i fornitori
salvano l'ora locale dell'allenamento (`startTimeLocal`, `start_date_local`),
quindi è già l'ora del posto in cui la corsa è avvenuta. `clock.day_bounds`
esiste apposta per dirlo, perché la conversione sbagliata è quella che viene in
mente per prima.

## La sync chiede solo quello che non ha

Prima: ventotto giorni per tre endpoint ogni notte — e `sync_training` ne
interroga quattro per giorno — cioè circa **centosettanta richieste per utente
ogni notte**, per riscrivere con gli stessi valori righe già in archivio che non
sarebbero più cambiate. Su un'API non documentata legata a un account
personale, il volume è il rischio operativo principale di questa app.

Adesso `_days_to_fetch` chiede i giorni mancanti più gli ultimi quattro (Garmin
ritocca sonno e riepilogo per un paio di giorni). Si passa a una quindicina di
richieste.

Le attività invece **si recuperano indietro**: al primo collegamento un anno,
poi solo da poco prima dell'ultima in archivio, a pagine di cinquanta con un
tetto di dieci. Prima si chiedevano sempre e solo le ultime cinquanta: chi
collegava Garmin con anni di storia ne otteneva cinquanta — due mesi scarsi per
chi si allena spesso, meno della finestra su cui si calcolano fitness e fatica —
e chi stava due mesi senza aprire l'app perdeva il resto per sempre.

## Il piano, e se lo stai seguendo

Il piano riceve **lo stesso briefing del coach in chat**. Prima riceveva un
estratto generico: VO2max, il «training status» di Garmin che per questo account
è vuoto, chilometri totali e dieci righe di attività. Niente soglie, FTP, LTHR,
carico né zone — mentre il system prompt gli diceva «usa le soglie dell'atleta,
non valori generici».

Ed è consapevole dello **sport principale**: prima diceva «allenatore di corsa»
anche a un ciclista, con le fasi di preparazione tarate sui chilometri.

`training.adherence()` confronta le sedute previste con quelle registrate. È
deterministico e costa una query. Il confronto è per **giorno**, non per tipo:
pretendere che un fartlek registrato come «corsa» corrisponda alla voce
«intervalli» vorrebbe dire segnare come mancata una seduta fatta. I giorni di
riposo non contano come sedute previste, e la seduta di oggi non è ancora
saltata. Sotto il 60% il piano ha smesso di descrivere la realtà e il briefing
lo dice.

## Un calcolo per richiesta

`summary.build` legge centottanta giorni di attività, risolve il profilo
fisiologico e srotola una serie PMC di centottanta punti. Su una singola
apertura di `/coach` girava **quattro volte**, perché quattro componenti diversi
lo chiedevano senza sapere l'uno dell'altro — ed è giusto che non lo sappiano.

`app/analysis/cache.py` è una memoria che dura quanto una richiesta HTTP
(`ContextVar` azzerato dal middleware). Non a tempo: un carico calcolato dieci
minuti fa è sbagliato dopo una sincronizzazione, e inseguire l'invalidazione
costerebbe più di quanto si risparmia. Dentro una richiesta i dati non cambiano
per definizione. Fuori da una richiesta — scheduler, CLI, test — resta spenta.

## Accesso, e da dove arrivano i dati

Sono **due cose separate**, e fino alla v2 non lo erano.

L'account è dell'app: email, password, nome. Il login verifica un hash bcrypt
locale e **non contatta nessun fornitore** — presidiato da
`test_login_never_touches_a_provider`, perché se tornasse una verifica remota
un Garmin irraggiungibile terrebbe fuori la gente da casa propria, e un utente
Strava un account Garmin non ce l'ha proprio.

La sorgente dei dati si collega **dopo**, in `/connect`, ed è il primo passo
dopo la registrazione (ci si finisce dentro, non si scopre da soli). Appena
collegata, **la prima sincronizzazione parte da sola**: arrivare su un'app
vuota dopo aver collegato l'orologio la fa sembrare rotta.

Registrazione **a invito**, e l'invito è un **link** (`/register?invite=...`),
non un codice da dettare: chi lo riceve ci clicca e trova il campo riempito.
Il recupero password non passa da un'email — non c'è SMTP e non lo si vuole —
ma dall'amministratore:
```bash
docker exec garmin-app python -m app.cli create-invite     # oppure da /admin
docker exec garmin-app python -m app.cli reset-password <email> <nuova>
```

## Le sessioni si possono revocare

Il cookie è firmato e contiene id utente **ed epoca** (`users.session_epoch`).
Alzare l'epoca di uno fa decadere all'istante ogni cookie già emesso per quella
persona, ed è ciò che succede quando la password cambia e quando un account
viene disattivato.

Prima non esisteva niente del genere, e le conseguenze erano due, entrambe
silenziose: «Disattiva» in `/admin` non buttava fuori chi era già dentro —
`is_active` si controllava solo nel login — e reimpostare la password di un
account compromesso lasciava valido per altri trenta giorni il cookie di chi lo
aveva preso. Adesso `require_user` verifica tutt'e tre le cose a ogni
richiesta: che l'utente esista, che sia attivo, che l'epoca combaci.

Chi cambia la password da solo si vede riemettere il cookie sul posto: a
restare fuori sono gli **altri** dispositivi, che è il punto.

## Cancellare il proprio account

Da `/settings`, riscrivendo il proprio indirizzo email. Spariscono utente,
allenamenti, sonno, recupero, corpo, obiettivi, piani, conversazioni, log; se
c'era Strava l'autorizzazione viene revocata, e la cartella dei token Garmin
sparisce dal disco. Restano i codici invito, che sono la traccia di chi ha
invitato chi e non contengono dati personali.

La cancellazione è **esplicita tabella per tabella e** con `ON DELETE CASCADE`
sulle chiavi esterne. La ridondanza è voluta: il cascade copre le tabelle che
qualcuno aggiungerà dopo dimenticando di metterle nell'elenco, l'elenco copre
il motore che il cascade non lo applica. Su un'operazione irreversibile, un
residuo silenzioso è il modo peggiore di sbagliare.

Nota su SQLite: le chiavi esterne le fa valere solo col pragma acceso, e di
default è spento. `app/db/database.py` lo accende per ogni connessione, così
lo sviluppo si comporta come la produzione. Di riflesso, `migrations/env.py`
lo **spegne** durante le migrazioni: il batch mode di Alembic ricrea le
tabelle, e una `DROP TABLE` su una tabella referenziata fallirebbe.

## Quello che non si può fare più di N volte

Il limite di frequenza sta in `app/security.py`, in memoria, dentro il
processo. Non in Caddy: con un container solo è equivalente, e qui la suite di
test può verificarlo.

| Rotta | Perché | Tetto |
|---|---|---|
| `POST /login`, `/register`, `/settings/password` | ogni tentativo è un bcrypt | 8 / 5 min, 5 / 10 min |
| `POST /connect/garmin` | ogni tentativo è un **login vero su Garmin**: senza limite questo server è un proxy per provare credenziali altrui, col nostro IP a prendersi il blocco | 5 / 10 min |
| `POST /chat/send`, `/plan/generate`, `/sync` | costano soldi o tempo | vedi `RULES` |
| `POST /telegram/webhook/...` | è l'unico endpoint pubblico | 60 / min |

Dietro il proxy l'indirizzo del chiamante arriva in `X-Forwarded-For`, che però
la può scrivere chiunque. Si legge **solo in produzione** (dove sappiamo che
davanti c'è Caddy) e se ne prende **l'ultimo** valore, perché Caddy aggiunge in
coda: quelli prima li può aver messi il client.

## L'AI parla solo di oggi

`refresh_daily_cache` chiama il modello **solo per la data corrente**. La
regola «una chiamata al giorno» reggeva finché il giorno era uno solo; da
quando `/coach` accetta `?day=`, ogni data mai vista era una riga di cache
nuova e quindi tre chiamate — due sul modello grande. Venti clic su «giorno
precedente», o un prefetch del browser sui link di navigazione, e il budget del
mese era andato.

Tre reti, non una: l'AI solo per oggi; `?day=` accettato solo fino a novanta
giorni indietro e mai nel futuro (`_requested_day`); un tetto giornaliero sulle
chiamate di tipo `coach`, come ce l'ha la chat.

`POST /ai/plan` non esiste più: era il residuo della pagina `/ai` della v1 e
generava un piano — l'operazione AI più costosa dell'app — senza controllare la
quota e senza registrarne il consumo.

## Segreti e intestazioni

L'app **si rifiuta di partire in produzione** con `SESSION_SECRET` mancante o
ancora quello di sviluppo, e senza `FERNET_KEY`. Produzione vuol dire
`PUBLIC_BASE_URL` in `https://`: si deduce da lì invece di chiedere una
variabile in più, così i `.env.prod` già scritti restano validi. In sviluppo
resta un avviso nel log. Dalla stessa proprietà dipende il flag `Secure` del
cookie, che su `http://localhost` non si può mettere.

Ogni risposta porta una **Content-Security-Policy**: `default-src 'self'`,
nessuna origine esterna, e gli `<script>` inline dei grafici autorizzati con un
nonce per richiesta invece che con `'unsafe-inline'`. Chart.js è stato portato
in `/static`: era l'unico script di terze parti, arrivava da un CDN senza
`integrity`, e girava su pagine che mostrano dati sanitari. `style-src`
tollera l'inline perché una ventina di attributi `style=` portano percentuali
calcolate dai dati, e un attributo `style` non esegue codice.

Gli errori interni non arrivano più a schermo: il testo delle eccezioni di
`garminconnect`, `stravalib` e degli SDK dei modelli finisce nel log, all'utente
va una frase fissa.

## Garmin **oppure** Strava

`app/providers/` è il posto dove si dichiara cosa un fornitore sa dare. Non è
un dettaglio implementativo: da lì discende cosa l'utente vede.

Strava è una piattaforma di attività, non un dispositivo: **non misura niente
di notte**. Niente sonno, HRV, frequenza a riposo, Body Battery. Tre dei cinque
fattori di prontezza e metà del briefing.

La regola scelta è che **quello che un utente non può avere, per lui non
esista**. Chi usa Strava non vede il gruppo *Salute* nel menu, e `/sleep`,
`/health`, `/body` rimandano al coach. Non pagine vuote con una spiegazione: una
pagina che si scusa è un errore travestito da informazione, e l'utente non ha
sbagliato niente. Su `/coach` il titolo diventa la forma invece della
prontezza — più piccolo, ma intero.

**La differenza si paga una volta sola**, nella tabella di confronto in
`/connect`, dove la scelta si fa a ragion veduta.

Una sorgente sola per utente (`UniqueConstraint("user_id")`): Strava importa da
Garmin, e tenerle entrambe vorrebbe dire contare ogni allenamento due volte.

Due cose che cadono da sole e non sono state programmate:
- la prontezza di un utente Strava copre 0,15 di peso su `MIN_COVERED_WEIGHT`
  di 0,45, quindi restituisce già «dati insufficienti» senza codice nuovo;
- il carico, le curve CTL/ATL/TSB e le zone restano interi, perché
  `app/analysis/` li calcola da durata, frequenza e potenza — cioè da quello
  che Strava dà. Sette pagine su undici non cambiano.

Il wrapper è [stravalib](https://github.com/stravalib/stravalib) 2.5.0, che
rinfresca i token da sé e rispetta i rate limit. **Il refresh token ruota** a
ogni rinnovo: `_fresh_token` scrive su database *prima* di restituire, o il
collegamento muore in silenzio al giro dopo.

> Nota sui termini: l'[API Policy di Strava](https://www.strava.com/legal/api_policy)
> §5.3 vieta l'uso dei dati «in connection with the... operation of any AI
> Application». Questa integrazione è stata fatta con quel fatto sul tavolo.

## Corsa o bici

`app/sports.py` è l'unico posto in cui si decide, per ogni tipo di attività,
come si chiama in italiano, che icona ha e **in che unità si misura**. Prima
`indoor_cycling` finiva a schermo così com'era, e un giro a 23 km/h veniva
mostrato come «2:35/km»: aritmeticamente giusto, praticamente illeggibile.

L'utente sceglie lo **sport principale** — corsa o bici — alla registrazione e
in `/settings`. È una preferenza di **lettura**: cambia quali primati hanno
senso (tempi sulle distanze contro velocità e potenza) e cosa si vede per
primo.

**Non filtra niente.** Il carico, le zone, il briefing e i tool dell'AI
continuano a considerare tutti gli sport, perché un giro in bici di tre ore è
carico anche per chi corre. Il prompt lo dice esplicitamente.

## Pagine

Il menu è **a gruppi**: quattordici voci in fila non sono un menu, sono un
elenco. Account, dispositivi e amministrazione non stanno nella barra laterale
ma nel menu del profilo in alto a destra — sono cose che si fanno una volta,
non ogni giorno.

| Gruppo | Pagina | URL | Contenuto |
|---|--------|-----|-----------|
| Oggi | Coach | `/coach` | Prontezza, allenamento di oggi, insight, obiettivo, progressi |
| Oggi | Coach AI | `/chat` | Chat con tool sui dati, risposta in streaming |
| Oggi | Panoramica | `/` | KPI e grafici |
| Allenamento | Attività | `/activities` | Storico, distribuzione delle intensità, dettaglio |
| Allenamento | Forma | `/fitness` | Fitness/fatica/forma, carico acuto, zone FC, primati, VO₂max |
| Allenamento | Piano | `/plan` | Piano generato, settimana corrente, adattamento giornaliero |
| Allenamento | Obiettivo | `/goals` | Il "laboratorio": tipo, parametri, data target, note |
| Salute | Sonno · Recupero · Corpo | `/sleep` `/health` `/body` | Grafici **con la lettura**: verdetto, evidenza, cosa fare |
| Account | Sorgente dati | `/connect` | Garmin **oppure** Strava, con il confronto di cosa danno |
| Account | Impostazioni | `/settings` | Soglie fisiologiche, notifiche, cambio password |
| Account | Dispositivi | `/devices` | Orologi, gear, badge (letti live) |
| Account | Amministrazione | `/admin` | Utenti, inviti, quote, spesa AI (solo admin) |

Due rotte sono state ritirate e rispondono `301`: `/performance` → `/fitness`
(confluita), `/ai` → `/chat` (era un banco di prova della v1 con un bottone che
chiamava un endpoint non più esistente).

## Dati

Time-series salvate nel DB, tutte con `user_id` e unicità composita `(user_id, giorno)`
— per le attività `(user_id, source, external_id)`, perché fornitori diversi
numerano gli id per conto proprio:
`activities`, `sleep_records`, `training_metrics`, `daily_wellness`,
`body_composition`, `daily_checkins` (le risposte del mattino).

`activities.rpe` sta sulla tabella delle attività e non in una tabella sua
perché `activity_load` lo legge per ogni attività di centottanta giorni: una
join in quel punto si sentirebbe. La sync scrive campo per campo e non lo tocca.

`users` porta email, `password_hash` e `session_epoch` (l'account dell'app e la
revoca delle sue sessioni), più il profilo fisiologico (`hr_max`, `hr_rest`, `lthr`, `ftp`,
`sex`, `birth_year`), tutto facoltativo. `activities.hr_zones_json` contiene i
secondi trascorsi in ciascuna zona FC.

Tabelle applicative: `users`, `provider_connections`, `invite_codes`,
`user_goals`, `training_plans`,
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
python -m app.cli delete-user <email> --yes   # cancella account e dati (irreversibile)
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
  `rollback()` prima di scrivere qualcos'altro nel gestore dell'eccezione;
- **i vincoli si chiamano diversi.** SQLite non nomina quelli dichiarati dentro
  la tabella, Postgres se li inventa concatenando le colonne. Lo stesso vincolo
  è `uq_activities_user_id` di qua e
  `activities_user_id_garmin_activity_id_key` di là: un `drop_constraint` con
  il nome fisso passa in sviluppo e fallisce in produzione. Si chiede il nome
  al motore (`_uq_name` nella migrazione `130d59b5783a`).

**Con `uvicorn --reload` acceso non si toccano i modelli.** Il riavvio chiama
`init_db()` → `create_all`, che crea le tabelle nuove **dai modelli**, con i
vincoli senza nome. Poi la migrazione Alembic trova la tabella già lì e muore
su `table ... already exists`, lasciando il database in uno stato ibrido. Prima
di lavorare sullo schema, ferma il server.

## Note tecniche

- Richiede **Python 3.11+**. Avvia sempre dal venv (`./venv/bin/...`).
- Segreti in `.env` (gitignored) e `.env.prod` sul server. Mai in git.
- Il parsing delle risposte Garmin è difensivo: i campi mancanti diventano
  `None`/`—`, la sync e le pagine non si interrompono.
- Ogni passo della pipeline è isolato: se la sync fallisce, coaching,
  gamification e notifiche proseguono comunque.
- I test non toccano mai la rete né il database reale: provider finti e
  `SessionLocal` reindirizzato. Due guardie in `tests/test_isolation.py` lo
  presidiano — `test_session_factory_points_at_the_test_database` e
  `test_no_test_can_reach_a_real_ai_provider`. La seconda è nata da un bug
  vero: `get_provider()` legge `AI_PROVIDER` dal `.env` di sviluppo, quindi
  ogni `refresh_daily_cache` chiamava davvero l'API di Anthropic. Il blocco sta
  in una fixture `autouse` di `conftest.py`, che azzera anche le chiavi.
- L'app funziona senza chiave AI: chat e coaching narrativo si disattivano da
  soli, tutto il resto resta deterministico.
