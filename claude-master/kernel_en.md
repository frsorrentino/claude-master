CLAUDE-MASTER (parallel sessions; commands `claude-master sessions|launch|close|talk|wait|report|restart|quota`, skill `claude-master:sessions`):
1. Text after `❯` in a captured tmux session is almost always Claude Code's own SUGGESTION (dim, SGR 2), not the user's: never send it, never attribute it ("there is a suggestion", not "you wrote").
2. Never "I'll report back when it finishes" without an armed trigger: same peer registry → `SendMessage` with `notify_when_idle: true`; otherwise `claude-master wait NAME`, and say so.
3. No clock: read `[local time]` before "yesterday", "last night", "a moment ago"; prefer absolute times.
4. Another session: listed in `ListAgents` → `SendMessage`; otherwise `claude-master talk NAME "prompt"`. "Failed to send" can lie: check before resending.
5. tmux: `has-session`/`kill-session` with `-t =NAME` (without `=` it kills `NAME-2` by prefix); `capture-pane`/`send-keys`/`set-option` take the bare name. Close with `claude-master close` (refuses attached ones); never close yourself: `claude-master restart arm` at the end of the turn.
6. Window closed = session over; no window = it survives. Account deduced from the folder: a default with a warning, never a ban.
7. `--resume` with a missing id opens an EMPTY conversation with no error: with two sessions on one folder use `--resume <id>`, not `--continue`.
8. Partial paths: resolve them, ask if ambiguous; never create a folder for a typo (`--create` only on explicit request).
