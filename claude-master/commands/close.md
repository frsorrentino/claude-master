---
description: Close a Claude session by tmux name (refuses attached ones), or the detached sessions stuck on a question with --abandoned
allowed-tools: Bash
---

```
claude-master close ${ARGUMENTS}
```

Report the output as-is. Never kill a session with `tmux kill-session` yourself: `close` refuses attached sessions for a reason, and a session must not close itself (use `claude-master restart arm` instead).
