#!/usr/bin/env python3
"""Verifica release.sh --check (RL1–RL5): preflight fermo su versione/badge incoerenti, suite
eseguita quando tutto combacia. Nessun push, nessun tag: --check si ferma prima. Gira su una
copia del repo in tmp con una suite finta al posto di quella vera (che altrimenti girerebbe
dentro sé stessa)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

root = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="cm-release-"))
copy = tmp / "repo"
shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests"))
(copy / "tests").mkdir()
marker = tmp / "marker"
(copy / "tests" / "zz-fake-verify.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
subprocess.run(["git", "init", "-q"], cwd=copy, check=True)
subprocess.run(GIT + ["add", "-A"], cwd=copy, check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "x"], cwd=copy, check=True)
pj = copy / "claude-master" / ".claude-plugin" / "plugin.json"
ver = json.loads(pj.read_text())["version"]


def run(v):
    # la copia del repo sta in una cartella temporanea: la fonte di claude-observe e' quella accanto al repo vero
    return subprocess.run(["bash", "release.sh", v, "--check"], cwd=copy, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "CLAUDE_OBSERVE_SRC": str(root.parent / "claude-observe")})


r = run("9.9.9")
T.check("RL1 plugin.json not at version → FAIL, suite not run",
        r.returncode != 0 and "plugin.json" in r.stdout + r.stderr and not marker.exists(), r.stdout + r.stderr)
r = run(ver)
T.check("RL2 consistent version → preflight passes, the suite runs, stops with CHECK OK",
        r.returncode == 0 and marker.exists() and "CHECK OK" in r.stdout, r.stdout[-600:] + r.stderr[-300:])
marker.unlink()
readme = copy / "README.md"
readme.write_text(readme.read_text().replace(f"version-{ver}-blue", "version-0.0.0-blue"))
r = run(ver)
T.check("RL3 README badge off → FAIL naming the badge, suite not run",
        r.returncode != 0 and "badge" in (r.stdout + r.stderr).lower() and not marker.exists(), r.stdout + r.stderr)
readme.write_text(readme.read_text().replace("version-0.0.0-blue", f"version-{ver}-blue"))
# RL4: la voce della versione che si rilascia ancora «unreleased» (15/09/2026: la 0.4.7 e' uscita con titolo
# e tag «v0.4.7 — unreleased», e il tag non si corregge)
cl = copy / "CHANGELOG.md"
dated = cl.read_text()
head = next(l for l in dated.splitlines() if l.startswith(f"- **{ver} — "))
cl.write_text(dated.replace(head, f"- **{ver} — unreleased.**", 1))
r = run(ver)
T.check("RL4 CHANGELOG entry of the version being released still «unreleased» → FAIL naming it, suite not run",
        r.returncode != 0 and "unreleased" in r.stdout + r.stderr and not marker.exists(), r.stdout + r.stderr)
marker.unlink(missing_ok=True)   # se RL4 ha fatto girare la suite, il marker non deve far passare RL4b da solo
cl.write_text(dated.replace(head, head + "\n\n- **9.9.8 — unreleased.**", 1))
r = run(ver)
T.check("RL4 an «unreleased» entry of ANOTHER version does not block: preflight passes, CHECK OK",
        r.returncode == 0 and marker.exists() and "CHECK OK" in r.stdout, r.stdout[-600:] + r.stderr[-300:])
# RL5 (17/09/2026): lo zip impacchetta la cartella del plugin dal disco — un file non tracciato li' dentro uscirebbe
# nel pacchetto pubblico (un docs/recap.md scritto per errore sotto scripts/, visto per caso prima della 0.4.13)
marker.unlink(missing_ok=True)
stray = copy / "claude-master" / "scripts" / "docs" / "recap.md"
stray.parent.mkdir(parents=True)
stray.write_text("privato\n")
r = run(ver)
T.check("RL5 an untracked file under the plugin folder → FAIL naming it, suite not run",
        r.returncode != 0 and "untracked" in r.stdout and "scripts/docs/recap.md" in r.stdout and not marker.exists(), r.stdout[-400:] + r.stderr[-200:])
shutil.rmtree(stray.parent)
(copy / "note-fuori-dal-plugin.txt").write_text("x\n")
r = run(ver)
T.check("RL5 an untracked file OUTSIDE the plugin folder does not block (it never ships): CHECK OK",
        r.returncode == 0 and "CHECK OK" in r.stdout, r.stdout[-400:] + r.stderr[-200:])
# RL6 (23/09/2026): lanciata da una sessione (niente terminale) una release VERA si ferma dopo il preflight se nessun ok
# dell'utente la copre, prima di zip, commit e push. Stato degli ok isolato in una cartella vuota.
okstate = Path(tempfile.mkdtemp(prefix="cm-release-ok-"))
okcfg = okstate / "config.json"
okcfg.write_text(json.dumps({"language": "it", "state_dir": str(okstate)}))
real = subprocess.run(["bash", "release.sh", ver], cwd=copy, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                      env={**os.environ, "CLAUDE_OBSERVE_SRC": str(root.parent / "claude-observe"), "CLAUDE_MASTER_CONFIG": str(okcfg)})
T.check("RL6 a real release run by a session without the user's ok stops right after the preflight, before tests, zip and push",
        real.returncode != 0 and "no ok from the user covers this release" in real.stdout and "== 2/6" not in real.stdout and "== 3/6" not in real.stdout,
        real.stdout[-600:] + real.stderr[-300:])
shutil.rmtree(tmp)
T.finish()
