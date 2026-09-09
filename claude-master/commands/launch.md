---
description: Launch a Claude session in a folder (tmux, terminal tab, Remote Control link); resolves partial folder names first, never creates a folder unless asked
allowed-tools: Bash, Skill
---

Follow the `claude-master:sessions` skill: resolve the folder the user named (partial names, typos) under the workspace root, ask if ambiguous, never pass `--create` unless the user explicitly asked to create it. Then run:

```
claude-master launch <absolute path> ${ARGUMENTS}
```

Report name, account, folder and the `claude.ai/code/session_…` link exactly as printed.
