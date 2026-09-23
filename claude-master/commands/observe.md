---
description: Record an observation on one of our tools (a limit, an error, a workaround) in its local box, or read and triage the box — errors of MCP and CLI calls are recorded by a hook already; use this for what a hook cannot see (skills without tools of their own, wrong results that looked fine, the workaround that worked)
allowed-tools: Bash
---

Errors of claude-master's own commands are recorded by a hook on their own (claude-observe, `claude-master/observe/`). Use this for what a hook cannot
see: a skill without tools of its own that misled you, a call that "succeeded" with a wrong result, and above all the
workaround that worked and your judgement (D our defect, L someone else's limit, S the site's behaviour).

```
claude-master observe ${ARGUMENTS}
```

Forms (TOOL is `claude-master`): `add TOOL "what happened" [--error "…"] [--workaround "…"] [--class D|L|S]`, `add --on ID --workaround "…"` to
enrich a record the hook wrote, `list [TOOL]`, `show ID`, `mark ID D|L|S|done`, `export TOOL`. Never put secrets,
passwords, tokens or a client's personal data in the text. To send: `report TOOL` prints ONE anonymized issue with all
the unsent observations of that tool (or a comment on an open issue about the same error); show it to the user as it
is, ask for a single yes, and only then run the `report TOOL --send HASH` it prints. Without gh it prints a prefilled
GitHub link for the user to open. What was sent is never offered again.
