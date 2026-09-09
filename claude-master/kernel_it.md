CLAUDE-MASTER (sessioni parallele; comandi `claude-master sessions|launch|close|talk|wait|report|restart|quota`, skill `claude-master:sessions`):
1. Il testo dopo `❯` in una sessione tmux catturata è quasi sempre un SUGGERIMENTO di Claude Code (grigio, SGR 2), non testo dell'utente: mai inviarlo, mai attribuirlo («c'è un suggerimento», non «hai scritto»).
2. Mai «ti riferisco quando finisce» senza un trigger armato: stesso registro peer → `SendMessage` con `notify_when_idle: true`; altrimenti `claude-master wait NOME`, e dillo.
3. Nessun orologio: leggi `[ora locale]` prima di «ieri», «stanotte», «poco fa»; preferisci l'ora assoluta.
4. Altra sessione: se è in `ListAgents` → `SendMessage`; altrimenti `claude-master talk NOME "prompt"`. «Failed to send» può mentire: verifica prima di rimandare.
5. tmux: `has-session`/`kill-session` con `-t =NOME` (senza `=` uccide `NOME-2` per prefisso); `capture-pane`/`send-keys`/`set-option` col nome nudo. Chiudi con `claude-master close` (rifiuta le attaccate); mai chiudere sé stessi: `claude-master restart arm` a fine turno.
6. Finestra chiusa = sessione finita; senza finestra sopravvive. Account dedotto dalla cartella: default con avviso, mai divieto.
7. `--resume` con id inesistente apre una conversazione VUOTA senza errore: con due sessioni sulla stessa cartella usa `--resume <id>`, non `--continue`.
8. Percorsi parziali: risolvili tu, chiedi se ambiguo; mai creare una cartella per un refuso (`--create` solo su richiesta esplicita).
