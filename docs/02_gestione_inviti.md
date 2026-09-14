# Gestione Inviti

L'applicazione Garmin Coach (v2) è multi-utente, ma l'accesso è strettamente su **invito**. Non esiste una pagina di registrazione pubblica per evitare accessi indesiderati e limitare il costo delle chiamate alle API AI.

## 1. Come creare un link d'invito

Per permettere a un nuovo utente di registrarsi, un amministratore deve generare un codice d'invito (e relativo link) tramite la CLI dell'applicazione.

Il comando base da lanciare dal server (o dal terminale locale connesso all'ambiente dell'app) è:

```bash
python -m app.cli create-invite
```

### Opzioni Avanzate

È possibile generare un invito con una **scadenza predefinita** (in giorni). Ad esempio, per un invito valido 30 giorni:

```bash
python -m app.cli create-invite --expires-days 30
```

## 2. Cosa restituisce il comando

Il comando `create-invite` **non restituisce solo il codice**. Restituisce l'**URL completo (link d'invito)** da mandare all'utente, ad esempio:

`https://tuo-dominio.com/login?invite=CODICE_GENERATO`

### Perché un link intero?
L'uso di un link (anziché un codice nudo e crudo) migliora l'UX. Quando l'utente clicca sul link, atterra direttamente sulla pagina `/login` con il campo "Codice di Invito" precompilato. In questo modo l'utente non deve capire da solo dove e come incollare il codice.

## 3. Gestione Utenti

Altri comandi utili dalla CLI per la gestione degli utenti (inclusi gli invitati):

- **Elencare gli utenti registrati:**
  ```bash
  python -m app.cli list-users
  ```
  *Mostra ID, email, stato (admin/disattivato) e data dell'ultima sincronizzazione.*

- **Promuovere un utente ad Admin:**
  ```bash
  python -m app.cli set-admin <email>
  ```

- **Reimpostare la password di un utente:**
  ```bash
  python -m app.cli reset-password <email> <nuova-password>
  ```
  *(Siccome l'app non prevede un flusso "ho dimenticato la password" via email per contenere l'infrastruttura, il reset è manuale da parte dell'admin).*
