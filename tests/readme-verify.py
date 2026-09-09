#!/usr/bin/env python3
"""Verifica README.md: ogni sottocomando del dispatcher è documentato come `claude-master <sub>`,
il badge di versione combacia con plugin.json, le sezioni obbligatorie ci sono, nessun nome di
fornitore (R1–R4)."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

root = Path(__file__).resolve().parent.parent
readme = (root / "README.md").read_text()
dispatcher = (root / "claude-master" / "scripts" / "claude-master").read_text()
# righe del case: `  launch|lancia)  exec ...` → il primo nome è quello inglese
subs = sorted({m.group(1) for m in re.finditer(r"^  ([a-z]+)(?:\|[a-z|-]+)?\)\s", dispatcher, re.M)})
subs = [s for s in subs if s not in ("help", "version", "root")]
missing = [s for s in subs if not re.search(rf"`claude-master {s}\b", readme)]
T.check("R1 every subcommand appears as `claude-master <sub>`", not missing, ", ".join(missing) or f"{len(subs)} subcommands")
ver = json.loads((root / "claude-master" / ".claude-plugin" / "plugin.json").read_text())["version"]
line3 = readme.splitlines()[2]
T.check("R2 version badge on line 3 matches plugin.json", f"version-{ver}-blue" in line3, line3)
for sec in ("## Quickstart", "## What it does", "## Commands", "## Configuration", "## Requirements",
            "## Terminal backends", "## How it works", "## Tests", "## License"):
    T.check(f"R3 section {sec}", sec in readme, "")
T.check("R4 no vendor name in the README", "pixelfarm" not in readme.lower(), "")
T.finish()
