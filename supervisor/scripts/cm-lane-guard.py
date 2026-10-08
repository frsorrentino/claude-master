#!/usr/bin/env python3
"""Hook PreToolUse (Bash): la guardia della corsia chiusa (fase 1, 03/10/2026).

Nega `git push`, `gh release create|upload|edit|delete`, `npm publish`, `claude plugin publish` e uno script
`release.sh` quando nella cartella del comando non c'e' un compito che li copre:
- un compito in corso (o approvato) di un piano approvato, senza `hold`, la cui cartella contiene quella del
  comando; oppure
- un compito con un ok registrato (`task approve`) nella stessa cartella, non ancora chiuso.
Non chiede un secondo ok: controlla soltanto. Spenta di default (`tasks.guard`), e senza registro non fa nulla:
in quei casi il comportamento e' quello di prima. Un errore della guardia non blocca mai il comando.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLOSED = re.compile(r"(?:^|[;&|(\s])(?:git\s+(?:-C\s+\S+\s+)?push\b(?!.*--dry-run)|gh\s+release\s+(?:create|upload|edit|delete)\b"
                    r"|npm\s+publish\b(?!.*--dry-run)|claude\s+plugin\s+publish\b|(?:\S*/)?release\.sh\b(?!.*--check))")


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def covered(con, cwd):
    """(coperto, motivo). Il compito copre la cartella se il comando gira dentro la sua."""
    real = os.path.realpath(cwd)
    for r in con.execute("SELECT t.id, t.plan, t.state, t.project, t.contract, p.approved_at FROM tasks t "
                         "LEFT JOIN plans p ON p.id = t.plan WHERE t.state IN ('running', 'approved')"):
        proj = os.path.realpath(r["project"])
        if real != proj and not real.startswith(proj.rstrip("/") + "/"):
            continue
        task = json.loads(r["contract"])["task"]
        if r["plan"] and r["approved_at"] and not task.get("hold"):
            return True, f"task {r['id']} of approved plan {r['plan']}"
        if con.execute("SELECT 1 FROM approvals WHERE task=? AND by_ IS NOT NULL", (r["id"],)).fetchone():
            return True, f"task {r['id']} approved"
    return False, ""


def decide(payload):
    """None = lascia passare; altrimenti il motivo del rifiuto."""
    cmd = str((payload.get("tool_input") or {}).get("command") or "")
    bare = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\1$", "", cmd, flags=re.S | re.M)   # un heredoc non e' un comando
    bare = re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", bare)   # ne' il testo fra virgolette (messaggi di commit…)
    if not CLOSED.search(bare):   # il caso comune: nessun caricamento di config
        return None
    cm = _load("cm-config")
    cfg = cm.load(warn=False)
    if not (cfg.get("tasks") or {}).get("guard"):
        return None
    tk = _load("cm-tasks")
    con = tk.connect(create=False)
    if con is None:
        return None
    ok, _ = covered(con, payload.get("cwd") or os.getcwd())
    if ok:
        return None
    return cm.msg(cfg, "guard.lane_closed", cmd=cmd.strip().splitlines()[0][:120])


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return 0
        reason = decide(payload)
    except Exception:   # noqa: BLE001 — la guardia non deve mai rompere una sessione
        return 0
    if reason:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": reason}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
