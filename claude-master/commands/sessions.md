---
description: Every Claude session running on this machine, of every account — status, window, channel to reach it (for the phone, where there is no terminal)
allowed-tools: Bash(claude-master:*)
---

Run and report the output AS-IS (it is already formatted; do not summarize):

```
claude-master sessions ${ARGUMENTS}
```

Only allowed addition: if a row says a session is stuck on a question with nobody watching, one sentence on what to do (`claude-master close --abandoned --dry-run`, or attach and answer).
