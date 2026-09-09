---
description: Restart the current session at the end of this turn, resuming the conversation (--clean for a fresh context, --switch-account to continue on the other account)
allowed-tools: Bash
---

```
claude-master restart arm ${ARGUMENTS}
```

Report the output as-is, then END THE TURN: the restart is executed by the Stop hook once this turn is over. Do not run anything else after arming. With `--switch-account`, say explicitly that from the restart on the conversation context goes to the API under the other organization.
