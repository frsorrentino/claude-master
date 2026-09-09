#!/usr/bin/env python3
"""Verifica release.sh --check (RL1–RL3): preflight fermo su versione/badge incoerenti, suite
eseguita quando tutto combacia. Nessun push, nessun tag: --check si ferma prima. Gira su una
copia del repo in tmp con una suite finta al posto di quella vera (che altrimenti girerebbe
dentro sé stessa)."""
import json
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
    return subprocess.run(["bash", "release.sh", v, "--check"], cwd=copy, capture_output=True, text=True, timeout=120)


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
shutil.rmtree(tmp)
T.finish()
