#!/usr/bin/env python3
"""Guardia della corsia chiusa (fase 1, 03/10/2026): hook PreToolUse su Bash.

Spenta o senza registro: nessun effetto (il comportamento di prima). Accesa: push, release e publish negati fuori
da un compito che li copre; permessi dentro un compito in corso di un piano approvato senza hold, o con un ok
registrato. Il testo fra virgolette e gli heredoc non contano."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="cm-guard-"))
proj = tmp / "proj"
(proj / "sub").mkdir(parents=True)
other = tmp / "other"
other.mkdir()
cfg = tmp / "config.json"
DB = tmp / "tasks.db"
ENV = dict(os.environ, CM_TASKS_DB=str(DB), CLAUDE_MASTER_CONFIG=str(cfg))


def config(guard):
    cfg.write_text(json.dumps({"language": "en", "tasks": {"guard": guard}}))


def hook(cmd, cwd=proj):
    p = subprocess.run([sys.executable, str(T.SCRIPTS / "cm-lane-guard.py")], capture_output=True, text=True, env=ENV, timeout=30,
                       input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(cwd), "hook_event_name": "PreToolUse"}))
    if p.returncode != 0:
        return "error"
    if not p.stdout.strip():
        return "allow"
    out = json.loads(p.stdout)["hookSpecificOutput"]
    return out["permissionDecision"] + ": " + out["permissionDecisionReason"]


def cm(*args, stdin=None):
    return subprocess.run([str(T.SCRIPTS / "claude-master"), *args], input=stdin, capture_output=True, text=True, env=ENV, timeout=60)


def node(nid, plan, project=proj, hold=False, lane="closed"):
    return {"id": nid, "title": f"publish {nid}", "plan": plan,
            "where": {"project": str(project), "session": None, "host": "local", "account": None},
            "check": {"cmd": "true", "cwd": None, "timeout_s": 10}, "perimeter": [], "lane": lane, "depends_on": [],
            "route": "session", "hold": hold, "attempts_max": 3, "data_class": "internal", "requested_by": "test"}


# G1 spenta, o accesa senza registro: tutto come prima
config(False)
cm("task", "list")   # crea il registro
T.check("G1 guard off (the default): git push passes even with a registry", hook("git push origin main") == "allow", "")
config(True)
DB.unlink()
T.check("G1 guard on but no registry: git push passes, and no registry is created", hook("git push origin main") == "allow" and not DB.exists(), "")
cm("task", "list")

# G2 accesa, nessun compito: negati i comandi di pubblicazione, il resto passa
denied = {c: hook(c) for c in ("git push origin main", "gh release create v1.0", "npm publish", "bash release.sh 0.6.0", "git -C x push")}
T.check("G2 guard on, no task: push, gh release, npm publish, release.sh → deny with the reason", all(v.startswith("deny: Closed lane:") for v in denied.values()), str(denied))
allowed = {c: hook(c) for c in ("git status", "git push --dry-run", "bash release.sh 0.6.0 --check", "git commit -m 'then git push'",
                                "git commit -F - <<'EOF'\nnotes: run git push later\nEOF", "gh release view v1")}
T.check("G2 not publishing: status, --dry-run, release --check, «git push» inside a quoted message or a heredoc, gh release view → allow",
        all(v == "allow" for v in allowed.values()), str(allowed))

# G3 piano approvato: il compito in corso apre la corsia nella sua cartella (e sotto), non altrove
plan = {"schema_version": 1, "id": "rel", "title": "release", "nodes": [node("pub", "rel"), node("held", "rel", project=other, hold=True)]}
(tmp / "plan.json").write_text(json.dumps(plan))
r = cm("plan", "approve", str(tmp / "plan.json"), "--by", "maintainer", "--text", "ok il piano")
T.check("G3 approved plan, task not started yet → still denied", r.returncode == 0 and hook("git push").startswith("deny"), r.stdout + r.stderr)
cm("task", "start", "pub")
T.check("G3 the task of the approved plan is running → push allowed in its folder and below it",
        hook("git push") == "allow" and hook("git push", proj / "sub") == "allow", "")
cm("task", "start", "held")
T.check("G3 a task marked hold («do not publish yet») does not open the lane, even running in an approved plan", hook("git push", other).startswith("deny"), "")
T.check("G3 another folder → denied", hook("git push", tmp).startswith("deny"), "")
cm("task", "cancel", "pub")
T.check("G3 the task is closed → denied again", hook("git push").startswith("deny"), "")

# G4 fuori da un piano: un ok registrato apre la corsia
c = {"schema_version": 1, "task": node("solo", None)}
cm("task", "add", "-", stdin=json.dumps(c))
cm("task", "wait-ok", "solo", "--what", "tag v1", "--where", "github")
T.check("G4 awaiting ok (asked, nobody approved) → denied", hook("git push --tags").startswith("deny"), "")
cm("task", "approve", "solo", "--by", "maintainer (phone)", "--text", "ok")
T.check("G4 the ok recorded → allowed", hook("git push --tags") == "allow", "")

# G5 un registro rotto non blocca mai
DB.write_text("not a database")
T.check("G5 a broken registry → allow (the guard never breaks a session)", hook("git push") == "allow", "")

# G6 hooks.json registra la guardia su Bash
hooks = json.loads((T.PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
T.check("G6 hooks.json: PreToolUse on Bash runs cm-lane-guard.py through py.sh",
        any(h.get("matcher") == "Bash" and "cm-lane-guard.py" in h["hooks"][0]["command"] and "py.sh" in h["hooks"][0]["command"] for h in hooks.get("PreToolUse", [])), "")

T.finish()
