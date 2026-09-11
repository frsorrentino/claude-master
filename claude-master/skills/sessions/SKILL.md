---
name: sessions
description: Use when the user asks to open, launch, start, list, close, restart or talk to a Claude Code session in another folder or account, to forward a screenshot/report to a project's session, to wait for another session, or to restore sessions after a reboot. Italian triggers "lancia claude in", "apri una sessione in/su", "avvia una sessione nella cartella", "crea la cartella X e lancia una sessione", "quali sessioni girano", "chiudi la sessione", "riavvia la sessione", "manda questo a", "segnala a", "novità da", "rispondi 2 a NOME", "2 a NOME" (after a Telegram notice that a session asks a question), "schermo NOME", "cosa sta facendo NOME". English triggers "launch a session in", "open a session on", "which sessions are running", "close the session", "restart this session", "send this to the session", "report to".
---

# Sessioni Claude su questa macchina (claude-master)

Tutto passa da un solo comando, `claude-master` (alias italiani da config: `lancia`, `sessioni`,
`chiudi`, `parla-con`, `segnala`, `riavvia`, `attacca`, `quote`). Le regole qui sotto sono state
pagate sul campo: valgono più dei comandi.

## Aprire una sessione su un'altra cartella

**1. Risolvi il percorso prima di lanciare.** L'utente dice percorsi parziali, abbreviati o con
refusi. `launch` accetta solo percorsi assoluti esistenti: la tolleranza sta qui.

- percorso assoluto ed esistente → usalo
- altrimenti cerca fra i candidati sotto la radice (`claude-master config --get workspace.root`),
  nelle cartelle di `workspace.project_dirs`, escludendo `workspace.excluded_dirs`:

      ls -d <radice>/*/ <radice>/*/*/ 2>/dev/null | grep -i <frammento>

- un solo candidato plausibile → proponilo e procedi; più candidati, o nessuno → **chiedi**

**2. Creazione: mai implicita.** Se la cartella non esiste, crearla è una decisione dell'utente,
non un ripiego per un nome che non hai trovato: un refuso diventa una cartella che resta lì per
mesi. `--create` solo se l'utente lo ha chiesto («crea la cartella X e lancia»), mostrando prima
il percorso completo.

**3. Lancia.**

    claude-master launch <percorso assoluto> [--create] [--continue|--resume <id>]
        [--account <nome>] [--no-window] [--bg] [--profile <nome>]

- l'account si **deduce dalla cartella** (`folder_map` in config) ed è un default con avviso,
  mai un divieto: `--account` (o `--personale`, `--professionale`) forza e viene rispettato
- `--continue` solo se l'utente ha chiesto di riprendere il lavoro precedente in quella cartella;
  il caso normale è una sessione pulita
- `--resume <id>` quando le conversazioni della cartella sono **più di una**: `--continue` prende
  sempre la più recente, e con due sessioni sulla stessa cartella le porta entrambe sulla stessa
  conversazione lasciando l'altra orfana (successo il 2026-08-24). Un id inesistente **non dà
  errore** in Claude: apre una conversazione vuota che sembra una ripresa riuscita; `launch` si
  ferma prima e stampa gli id veri con peso e data
- `--profile <nome>`: argomenti, modello, effort, variabili d'ambiente da `profiles.<nome>` in
  config (per esempio uno scan economico in background)
- `--bg`: lavori muti (scansioni, batch senza domande) — niente tmux, niente finestra, niente
  colore; si segue con `claude agents` / `claude attach <id>`; nasce **idle**, va nutrita con un
  prompt (`SendMessage` dallo stesso registro peer)

**4. Riferisci** nome tmux, account, cartella e il link `https://claude.ai/code/session_…` che
`launch` stampa (letto dal registro peer): porta dritto alla sessione, dal telefono come dal
browser. Se `launch` fallisce, riporta il suo messaggio: dice cosa fare.

Una scheda del terminale si apre sul desktop (backend da config: Terminale di ChromeOS via
garcon, gnome-terminal, kitty, iTerm2, Terminal.app, Windows Terminal). Su ChromeOS, con
`terminal.open_as_tab` (default), la sessione nasce come SCHEDA della finestra del Terminale già
aperta (quella senza scheda iniziale, se c'è): una finestra nuova solo quando non ce n'è nessuna
con una shell, e allora una sola per tutte. Costa ~20 s (duplicazione + segnaposto): non è lenta,
sta aspettando la scheda. Il titolo della scheda
porta un'icona: **forma = account, colore = quale sessione**. Chiudere quella scheda chiude la
sessione: è la regola unica. Con `--no-window` la sessione nasce senza vista e sopravvive: è
così che si ottiene una sessione da pilotare solo dal telefono.

## Quali sessioni girano

    claude-master sessions [--json] [--watch]

Enumera tutte le sessioni di tutti gli account (registro peer `sessions/<pid>.json` + processi),
con STATO (`busy`/`idle`/`waiting`), VISTA (`aperta`/`STACCATA`), CANALE (`(questa)` /
`nativo` / `talk`) e la nota «aspetta una risposta»: una sessione staccata ferma su una domanda è
lavoro fermo, non in corso — l'unico caso in cui chiuderla non interrompe niente.

## Parlare con un'altra sessione

Regola di precedenza, deterministica:

| il bersaglio… | usa |
|---|---|
| compare in `ListAgents` (stesso registro peer) | **`SendMessage`**, con `notify_when_idle: true` |
| non compare (altro registro, o fuori) | **`claude-master talk <nome> "prompt"`** |

`sessions` ha la colonna CANALE che applica la regola al posto tuo. `talk` consegna nel socket
inbox della sessione e legge la risposta dal suo transcript (`--wait`, `--no-wait` per non
aspettare); se la sessione non ha un socket, scrive nel riquadro tmux e legge lo schermo, dopo
aver controllato che nella casella non ci sia già del testo **digitato** (`--force` lo butta via).

**Mai scrivere «ti riferisco quando finisce» senza un trigger armato**: con `SendMessage` usa
`notify_when_idle: true` (arriva davvero, anche se il messaggio viene trattenuto); altrimenti
`claude-master wait <nome>` in un `Monitor`, oppure di' esplicitamente «chiedimi "novità?"».
Il risultato di `SendMessage` **può mentire** («Failed to send» a messaggio arrivato): verifica
sullo schermo o con `sessions` prima di rimandare, o consegni due volte.

**Il testo nel campo `❯` di una sessione catturata non è dell'utente.** Claude Code propone da
sé un messaggio successivo plausibile, in grigio (SGR 2): riferirlo come «hai digitato» mette in
bocca all'utente una decisione che non ha preso, e nel caso peggiore fa premere Invio su
un'azione che nessuno ha autorizzato. Chiamalo «un suggerimento di Claude Code»; inviare quel
testo solo se l'utente lo ripete in questa conversazione.

## Dal telefono a sessioni chiuse

Quando nessuna sessione è viva il plugin telegram non ascolta: `claude-master bot` (cron ogni
minuto, stesso bot, `bot.enabled` in config) risponde a `/master`, `/launch <frammento>`,
`/sessions`, `/recap`, e a `/start` con un bottone «/master». Resta muto finché una sessione ascolta Telegram. Dopo un riavvio della macchina
serve il login a mano: copre «sessioni chiuse, macchina sveglia».

## Rispondere alla domanda di un'altra sessione

Una sessione ferma su `AskUserQuestion` (o su un permesso) aspetta una scelta nel suo terminale:
un messaggio nell'inbox NON la sblocca. `claude-master answer NOME --show` legge la domanda e le
opzioni numerate; `claude-master answer NOME 2` sceglie la 2 (più numeri = più domande di fila;
`--text "…"` per «Type something.»). Dal telefono: «rispondi 2 a progetto-x» → la master esegue
`answer progetto-x 2` e riferisce la riga «risposto 2. …». Mai scegliere al posto dell'utente.

Quando una sessione si ferma, l'hook `PermissionRequest` manda da solo alle chat Telegram autorizzate
un avviso «❓ «progetto-x» chiede — …» con le opzioni numerate e la riga «rispondi «2 a progetto-x»
alla master» (`hooks.ask_notify` in config; testo, non tastiera inline: un tap arriverebbe al plugin
telegram, non a noi). L'utente risponde a quell'avviso con «2 a progetto-x» o «rispondi 2 a
progetto-x»: è un ordine da eseguire con `answer`, non una domanda da discutere. Se il numero non
esiste più (`answer` lo dice), riferisci le opzioni con `--show` e aspetta.

## Lo schermo di una sessione dal telefono

    claude-master screen <nome> [--lines N]      le ultime 30 righe del suo terminale

«schermo progetto-x» / «cosa sta facendo progetto-x» → esegui e riporta l'output **in un blocco di
codice**, senza riassumerlo (l'utente vuole vedere, non sentirsi raccontare). Ricorda: il testo dopo
`❯` è un suggerimento di Claude Code, non dell'utente.

## Segnalazioni dal telefono

    claude-master report <progetto> <immagine|-> "testo" [--no-launch]

Il gesto più frequente: uno screenshot di un cliente va nel progetto giusto e la sua sessione
deve riceverlo col testo. `<progetto>` è un pezzo del nome della cartella (`sito`, `cliente-a`):
esatto, poi prefisso, poi sottostringa. L'immagine finisce in `docs/segnalazioni/<data>-<slug>`
(o `report.subdir`), la sessione viene lanciata se manca, il testo consegnato senza aspettare.
Le foto arrivate via Remote Control hanno già un percorso su disco: passalo a `report`.

## Chiudere

    claude-master close <nome>                  chiude quella sessione
    claude-master close --abandoned [--dry-run] quelle staccate E ferme su una domanda
    /exit                                       dall'interno

`close` rifiuta le sessioni **attaccate**: se qualcuno la guarda, la sta usando. Una sessione
**non può chiudere sé stessa**: un kill da un tool arriva a turno aperto e lascia nel transcript
una chiamata senza risposta, che `--continue` poi riprende monca. Con tmux usa sempre
`-t =NOME` per `has-session` e `kill-session` (senza `=` aggancia per prefisso: l'08/09/2026 è
morta `fable-director-2` al posto di `fable-director`); `capture-pane`, `send-keys` e
`set-option` invece vogliono il nome nudo.

## Riavviare la sessione corrente

Quando cambia ciò che si legge solo all'avvio (settings, hook, `additionalDirectories`):

    claude-master restart arm                     riparte con --continue a fine turno
    claude-master restart arm --clean             contesto azzerato, memoria intatta (prima: stato su disco)
    claude-master restart arm --switch-account    stessa conversazione sull'altro account (T51)

Arma e basta: la chiusura la esegue lo Stop hook a turno finito, l'unico istante sicuro. Un flag
per sessione: più sessioni possono armare insieme (`claude-master restart list` le elenca).
Esito e link in `restart.log` (config): la sessione che li stamperebbe è morta prima che la
nuova esistesse. Il session id è nuovo: nell'app compare una voce nuova. `--switch-account`
copia il transcript nella `projects` dell'altro account e lo riprende lì: da quel momento il
contesto va all'API sotto l'altra organizzazione — è una scelta dell'utente, mai un automatismo.

## Coda di lavori per una sessione

    claude-master queue <nome> "prompt" [--expires <minuti>]

La sessione riceve la voce al prossimo turno finito (Stop hook, una per turno, con scadenza):
per i lavori in sequenza senza presidio. Mai per rilanciare all'infinito: la coda si svuota.

## Passare il lavoro a un'altra sessione (handoff)

1. chiedi alla sessione che lascia (via `SendMessage`/`talk`) di scrivere
   `docs/handoff-<data>.md`: stato, file toccati, cose aperte, comandi per verificare;
2. aspetta che sia idle (`notify_when_idle` o `wait`);
3. consegna alla sessione che riceve il percorso del file con le istruzioni.
Mai riscrivere il contesto a mano fra le due.

## Ripristino dopo un riavvio

Il registro (`claude-master registry --show`) fotografa le sessioni vive a ogni avvio/chiusura e
periodicamente. Accanto c'è la fotografia «ultimo insieme buono» (`registry --good`): cresce con le
sessioni, non dimagrisce mai da sola (finestre chiuse a mano, crash), una sessione ne esce solo con
`close` o `/exit`. `claude-master restore [--dry-run] [--yes]` propone l'UNIONE delle due liste
dicendo per ciascuna se viene dal registro o dalla fotografia e con quale data: se una sessione
della fotografia è vecchia e non serve, dillo all'utente invece di rilanciarla. Le rilancia con
`--continue`, in parallelo, quella della radice per ultima; salta le vive e le cartelle sparite.
L'innesco sta nella shell: alla prima shell fuori da tmux dopo un riavvio recente.

## Tempo

Non hai un orologio. L'hook `[ora locale]` inietta l'ora a ogni prompt: leggila prima di dire
«ieri», «stanotte», «poco fa», «da ore». Preferisci l'ora assoluta («ferme dalle 02:41»). La
mezzanotte non è un confine per chi ti legge: le 02:40 sono ancora «la sera di ieri» nel
vissuto, «oggi» nel calendario. Di' l'ora, non il giorno.

## Note

- Le sessioni partono con gli argomenti di `session.claude_args` (qui `--dangerously-skip-permissions`):
  il punto critico è *quale* cartella apri; verificare il percorso è il solo controllo che resta.
- La radice dei workspaces fa eccezione: la sessione si chiama sempre `master` (config
  `workspace.root_session_name`): la plancia, non un progetto.
- Il nome è quello della cartella con `.` e `:` convertiti in `-` (`sito.com` → `sito-com`,
  `nome-2` per la seconda sessione sulla stessa cartella) ed è il titolo che l'app mostra.
- Su una cartella mai aperta Claude chiede la fiducia (e, su una config nuova, l'accettazione del
  bypass): `launch` li rileva e risponde da solo, altrimenti la sessione arriverebbe sul telefono
  bloccata.
- Ogni sessione occupa 400–600 MB: con poca memoria controlla `free -m` prima di aprirne diverse.
- `claude-master quota` mostra quanto è pieno il serbatoio di ciascun account con l'età della
  lettura: un numero vecchio creduto fresco fa decidere sulla cosa sbagliata.
