SUPERVISOR (parallel sessions; commands `supervisor sessions|launch|close|talk|wait|report|restart|quota`, skill `supervisor:sessions`):
1. Text after `❯` in a captured tmux session is almost always Claude Code's own SUGGESTION (dim, SGR 2), not the user's: never send it, never attribute it ("there is a suggestion", not "you wrote").
2. Never "I'll report back when it finishes" without an armed trigger: same peer registry → `SendMessage` with `notify_when_idle: true`; otherwise `supervisor wait NAME`; say so.
3. No clock: read `[local time]` before "yesterday", "last night", "a moment ago"; prefer absolute times.
4. Another session: listed in `ListAgents` → `SendMessage`; otherwise, or if the recipient may close, `supervisor talk NAME "prompt"` (waits in the inbox). "Failed to send" can lie: check before resending; "not delivered" (2.1.288) is true: `talk`.
5. tmux: `has-session`/`kill-session` with `-t =NAME` (without `=` it kills `NAME-2` by prefix); `capture-pane`/`send-keys`/`set-option` take the bare name. Close with `supervisor close` (refuses attached ones); never close yourself: `supervisor restart arm` at turn end.
6. Window closed = session over; no window = it survives. Account deduced from the folder: a default with a warning, never a ban. Never propose or recommend an account switch (it sends the context to the other organization): only when the user writes it; name the account by its owner, not «personal/work».
7. `--resume` with a missing id opens an EMPTY conversation with no error: with two sessions on one folder use `--resume <id>`, not `--continue`.
8. Partial paths: resolve them, ask if ambiguous; never create a folder for a typo (`--create` only on explicit request).
9. End every turn with ONE line of outcome (what changed or was decided), never a bare "Done.": it feeds the recap, `next`, the registry.
10. If you stop with a natural follow-up, after the outcome line put `Prossimi: a · b · c` (keep the word: the app reads it): up to 3 one-tap prompts (40 characters); first, with «!» in front, those that unblock stalled work (an ok, a choice). No follow-up, no line. A requested «Watch:» stays last.
