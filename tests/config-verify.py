#!/usr/bin/env python3
"""Verifica cm-config.py: default, merge, --sh, --get, init, doctor, shim.

C1  default senza file: language en, root ~, un account `default`
C2  merge dal file + default guidati dalla lingua (prefisso ora locale)
C3  --get con chiave puntata, ~ espansa con CM_HOME
C4  --sh: export CM_* valutabili da bash (liste, dict annidati, TSV)
C5  --dump-defaults == config.example.json (sincronia)
C6  chiave sconosciuta nel file → avviso, exit 0; in --get → exit 1
C7  catalogo messaggi: stesse chiavi it/en, msg() con segnaposto
C8  init su macchina finta: backend, radice, due account, prefisso, wrapper, forma
C9  init: mappa cartelle, cartelle progetto, cartelle escluse
C10 init: stato legacy condiviso (D4) vs macchina pulita
C11 init: chrome-bridge cli, lingua dai settings
C12 init --yes scrive; secondo giro rifiuta senza --force; --dry-run non scrive
C15 init --tmux: blocco da tmux.keybindings; --yes accoda a ~/.tmux.conf una volta sola
C13 doctor: WARN hook duplicato, WARN chrome-bridge assente, FAIL account mancante
C14 shim: risolve la radice da installed_plugins.json ed esegue il dispatcher
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = T.tmpdir()
home = T.fake_home(tmp)
machine = T.fake_machine(tmp, home)
cfg_missing = Path(tmp) / "no-such-config.json"

# C1
r = T.run_config(["--get", "language"], home, cfg_missing)
T.check("C1 default language en", r.stdout.strip() == "en", r.stdout + r.stderr)
r = T.run_config(["--get", "workspace.root"], home, cfg_missing)
T.check("C1 default root = HOME", r.stdout.strip() == str(home), r.stdout + r.stderr)
r = T.run_config(["--get", "accounts"], home, cfg_missing)
T.check("C1 default one account", '"default"' in r.stdout and '"config_dir"' in r.stdout, r.stdout + r.stderr)

# C2
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({"language": "it",
                           "accounts": {"personale": {"config_dir": "~/.claude"},
                                        "professionale": {"config_dir": "~/.claude-pixel", "tmux_prefix": "pix-"}},
                           "default_account": "personale",
                           "workspace": {"root": "~/Desktop/workspaces"},
                           "folder_map": [{"path": "~/Desktop/workspaces/pixelfarm", "account": "professionale"}],
                           "hooks": {"local_time": {"format": "%H:%M"}}}))
r = T.run_config(["--get", "hooks.local_time.prefix"], home, cfg)
T.check("C2 lang-driven default prefix", r.stdout.strip() == "[ora locale]", r.stdout + r.stderr)
r = T.run_config(["--get", "hooks.local_time.format"], home, cfg)
T.check("C2 file overrides lang default", r.stdout.strip() == "%H:%M", r.stdout + r.stderr)
r = T.run_config(["--get", "accounts.professionale.shape"], home, cfg)
T.check("C2 account field default (shape)", r.stdout.strip() == "square", r.stdout + r.stderr)
r = T.run_config(["--get", "accounts.personale.shape"], home, cfg)
T.check("C2 first account circle", r.stdout.strip() == "circle", r.stdout + r.stderr)
r = T.run_config(["--get", "report.subdir"], home, cfg)
T.check("C2 lang-driven report subdir", r.stdout.strip() == "docs/segnalazioni", r.stdout + r.stderr)

# C3
r = T.run_config(["--get", "accounts.professionale.config_dir"], home, cfg)
T.check("C3 ~ expanded", r.stdout.strip() == str(home / ".claude-pixel"), r.stdout + r.stderr)
r = T.run_config(["--get", "tabs.colors.circle"], home, cfg)
T.check("C3 list as json", r.stdout.strip().startswith("[") and "🔴" in r.stdout, r.stdout + r.stderr)

# C4
r = T.run_config(["--sh"], home, cfg)
sh = r.stdout
T.check("C4 --sh exit 0", r.returncode == 0, r.stderr)
T.check("C4 CM_LANGUAGE", "export CM_LANGUAGE='it'" in sh or 'export CM_LANGUAGE="it"' in sh, sh[:400])
T.check("C4 CM_ACCOUNTS_KEYS", "CM_ACCOUNTS_KEYS='personale professionale'" in sh, sh[:2000])
T.check("C4 nested account var expanded",
        f"CM_ACCOUNTS_PROFESSIONALE_CONFIG_DIR='{home}/.claude-pixel'" in sh, sh[:3000])
T.check("C4 folder map TSV", f"CM_FOLDER_MAP='{home}/Desktop/workspaces/pixelfarm\tprofessionale" in sh, sh[:3000])
T.check("C4 list space-joined", "CM_TABS_COLORS_CIRCLE='🔴 🟠 🟡 🟢 🔵 🟣 ⚪'" in sh, sh[:3000])
T.check("C4 bool as true/false", "CM_SESSION_REMOTE_CONTROL='true'" in sh, sh[:3000])
b = subprocess.run(["bash", "-c", f'eval "$(python3 {T.SCRIPTS}/cm-config.py --sh)"; echo "$CM_LANGUAGE|$CM_ACCOUNTS_PERSONALE_TMUX_PREFIX|$CM_TALK_QUIET_S"'],
                   capture_output=True, text=True,
                   env={"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg)})
T.check("C4 eval in bash", b.stdout.strip() == "it||6", b.stdout + b.stderr)

# C5
r = T.run_config(["--dump-defaults"], home, cfg_missing)
example = (T.PLUGIN / "config.example.json").read_text() if (T.PLUGIN / "config.example.json").exists() else ""
T.check("C5 config.example.json in sync with defaults", r.stdout.strip() == example.strip(),
        "run: cm-config.py --dump-defaults > claude-master/config.example.json")

# C6
bad = Path(tmp) / "bad.json"
bad.write_text(json.dumps({"language": "it", "nonexistent": 1, "talk": {"typo": 2}}))
r = T.run_config(["--get", "language"], home, bad)
T.check("C6 unknown keys warn, still works", r.returncode == 0 and "nonexistent" in r.stderr and "talk.typo" in r.stderr,
        r.stdout + r.stderr)
r = T.run_config(["--get", "talk.typo"], home, bad)
T.check("C6 --get unknown key exit 1", r.returncode == 1, r.stdout + r.stderr)

# C7
it = json.loads((T.PLUGIN / "messages" / "it.json").read_text())
en = json.loads((T.PLUGIN / "messages" / "en.json").read_text())
T.check("C7 same message keys it/en", set(it) == set(en), f"only it: {set(it)-set(en)}; only en: {set(en)-set(it)}")
r = T.run_config(["--messages"], home, cfg)
T.check("C7 --messages exports MSG_*", "export MSG_" in r.stdout and r.returncode == 0, r.stdout[:300] + r.stderr)
r = T.run_config(["--msg", "init.detected", "key=x", "value=y", "source=z"], home, cfg)
T.check("C7 --msg formats placeholders", "x" in r.stdout and "y" in r.stdout and "z" in r.stdout and "{" not in r.stdout,
        r.stdout + r.stderr)

# C8 init on the fake machine
r = T.run_config(["init", "--dry-run", "--json"], home, cfg_missing, machine)
T.check("C8 init exit 0", r.returncode == 0, r.stderr[-800:])
try:
    out = json.loads(r.stdout)
    detected, sources = out["config"], out["sources"]
except Exception as e:  # noqa: BLE001
    detected, sources = {}, {}
    T.check("C8 init --json parses", False, f"{e}: {r.stdout[:300]} {r.stderr[-300:]}")
acc = detected.get("accounts", {})
T.check("C8 backend chromeos", detected.get("terminal", {}).get("backend") == "chromeos", str(detected.get("terminal")))
T.check("C8 root from registry", detected.get("workspace", {}).get("root") == "~/Desktop/workspaces", str(detected.get("workspace")))
T.check("C8 two accounts", set(acc) == {"personale", "professionale"}, str(list(acc)))
T.check("C8 professionale config_dir", acc.get("professionale", {}).get("config_dir") == "~/.claude-pixel", str(acc))
T.check("C8 professionale prefix pix- (from processes)", acc.get("professionale", {}).get("tmux_prefix") == "pix-", str(acc))
T.check("C8 personale prefix empty", acc.get("personale", {}).get("tmux_prefix") == "", str(acc))
T.check("C8 shell_command claude-pixel (from .bashrc)", acc.get("professionale", {}).get("shell_command") == "claude-pixel", str(acc))
T.check("C8 shapes circle/square", acc.get("personale", {}).get("shape") == "circle" and acc.get("professionale", {}).get("shape") == "square", str(acc))
T.check("C8 default_account personale", detected.get("default_account") == "personale", str(detected.get("default_account")))
T.check("C8 every value has a source", all(k in sources for k in ("terminal.backend", "workspace.root", "accounts.professionale.tmux_prefix")), str(list(sources))[:300])
T.check("C8 no 'pixelfarm' in plugin defaults", "pixelfarm" not in T.run_config(["--dump-defaults"], home, cfg_missing).stdout)

T.check("C8 session.claude_args from live sessions (T74)", detected.get("session", {}).get("claude_args") == ["--dangerously-skip-permissions"], str(detected.get("session")))
# C9
fm = detected.get("folder_map", [])
T.check("C9 folder_map pixelfarm→professionale", any(e.get("path") == "~/Desktop/workspaces/pixelfarm" and e.get("account") == "professionale" for e in fm), str(fm))
T.check("C9 folder_map personali→personale", any(e.get("path") == "~/Desktop/workspaces/personali" and e.get("account") == "personale" for e in fm), str(fm))
pd = detected.get("workspace", {}).get("project_dirs", [])
T.check("C9 project_dirs from sessions", set(pd) >= {"personali", "pixelfarm/clienti", "pixelfarm/nostri"}, str(pd))
ex = detected.get("workspace", {}).get("excluded_dirs", [])
T.check("C9 excluded_dirs with _archivio/_prod_backups", {"_archivio", "_prod_backups", ".git", "node_modules"} <= set(ex), str(ex))

# C10
T.check("C10 legacy: registry NOT shared (legacy labels differ)", detected.get("registry", {}).get("file") == "~/.claude/sessions.json", str(detected.get("registry")))
T.check("C10 legacy: colors path shared", detected.get("tabs", {}).get("color_registry") == "~/.claude/colori-sessione", str(detected.get("tabs")))
T.check("C10 legacy: placeholder path", detected.get("tile", {}).get("placeholder_file") == "~/.claude/prossima-sessione", str(detected.get("tile", {}).get("placeholder_file")))
tmp2 = T.tmpdir()
home2 = T.fake_home(tmp2, legacy=False, second_account=False, workspace=False, language=None)
machine2 = T.fake_machine(tmp2, home2, chromeos=False, procs=[])
r2 = T.run_config(["init", "--dry-run", "--json"], home2, Path(tmp2) / "none.json", machine2)
try:
    d2 = json.loads(r2.stdout)["config"]
except Exception:  # noqa: BLE001
    d2 = {}
    T.check("C10 clean init parses", False, r2.stdout[:300] + r2.stderr[-300:])
T.check("C10 clean: state_dir default", d2.get("state_dir") == "~/.local/state/claude-master", str(d2.get("state_dir")))
T.check("C10 clean: registry under state_dir", d2.get("registry", {}).get("file") == "~/.local/state/claude-master/sessions.json", str(d2.get("registry")))
T.check("C10 clean: backend gnome (gnome-terminal in PATH)", d2.get("terminal", {}).get("backend") == "gnome", str(d2.get("terminal")))
T.check("C10 clean: one account named personal (lang en)", list(d2.get("accounts", {})) == ["personal"], str(d2.get("accounts")))
T.check("C10 clean: root = ~", d2.get("workspace", {}).get("root") == "~", str(d2.get("workspace")))

# C11
T.check("C11 chrome-bridge cli from .claude.json", detected.get("tile", {}).get("chrome_bridge_cli") == "~/cb/server/cli.js", str(detected.get("tile", {}).get("chrome_bridge_cli")))
T.check("C11 language it from settings", detected.get("language") == "it", str(detected.get("language")))
T.check("C11 shell wrappers", detected.get("shell", {}).get("wrappers") == {"claude": "personale", "claude-pixel": "professionale"}, str(detected.get("shell", {}).get("wrappers")))
T.check("C11 shell aliases italian", detected.get("shell", {}).get("aliases", {}).get("lancia") == "launch", str(detected.get("shell", {}).get("aliases")))
T.check("C11 tmux keybindings from .tmux.conf", detected.get("tmux", {}).get("keybindings") == {"tile": "a", "merge": "u", "move_arrows": True}, str(detected.get("tmux")))

# C12
target = Path(tmp) / "written" / "config.json"
r = T.run_config(["init", "--dry-run"], home, target, machine)
T.check("C12 --dry-run writes nothing", not target.exists() and r.returncode == 0, r.stderr[-300:])
T.check("C12 --dry-run prints value and source", "terminal.backend" in r.stdout and "chromeos" in r.stdout, r.stdout[:500])
r = T.run_config(["init", "--yes"], home, target, machine)
T.check("C12 --yes writes config", target.exists() and r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
written = json.loads(target.read_text()) if target.exists() else {}
T.check("C12 written config reloads identically", written.get("accounts", {}).get("professionale", {}).get("tmux_prefix") == "pix-", str(written.get("accounts")))
r = T.run_config(["init", "--yes"], home, target, machine)
T.check("C12 second init refuses without --force", r.returncode != 0 and "--force" in (r.stdout + r.stderr), r.stdout + r.stderr)
r = T.run_config(["init", "--yes", "--force"], home, target, machine)
T.check("C12 --force overwrites", r.returncode == 0, r.stderr[-300:])

# C13 doctor
r = T.run_config(["doctor"], home, target, machine)
T.check("C13 doctor exit 0 on fake machine", r.returncode == 0, r.stdout + r.stderr)
T.check("C13 WARN duplicate local-time hook (both accounts)", r.stdout.count("WARN") >= 2 and "ora locale" in r.stdout.lower() or "local-time" in r.stdout, r.stdout)
T.check("C13 INFO legacy scripts present", "legacy" in r.stdout.lower(), r.stdout)
T.check("C13 PASS chrome-bridge cli", "PASS" in r.stdout and "cli.js" in r.stdout, r.stdout)
broken = json.loads(target.read_text())
broken["accounts"]["professionale"]["config_dir"] = "~/.claude-nope"
broken["tile"]["chrome_bridge_cli"] = "~/nope/cli.js"
(Path(tmp) / "broken.json").write_text(json.dumps(broken))
r = T.run_config(["doctor"], home, Path(tmp) / "broken.json", machine)
T.check("C13 FAIL missing account dir → exit 1", r.returncode == 1 and "FAIL" in r.stdout and ".claude-nope" in r.stdout, r.stdout)
T.check("C13 WARN missing chrome-bridge cli", "WARN" in r.stdout and "nope/cli.js" in r.stdout, r.stdout)
r = T.run_config(["doctor"], home, cfg_missing, machine)
T.check("C13 WARN no config → suggests init", r.returncode == 0 and "init" in r.stdout, r.stdout)

VER_CHECKOUT = json.loads((T.PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
# C13b doctor: plugin nella cache via `claude plugin list --json` (finto: CM_FAKE_PLUGIN_LIST)
pl = Path(tmp) / "plugins.json"
pl.write_text(json.dumps([{"id": "claude-master@claude-master-dev", "version": "0.0.1", "enabled": True}]))
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b cache older than the checkout → WARN with both versions and the update command", "WARN" in r.stdout and "0.0.1" in r.stdout and "claude plugin update claude-master" in r.stdout, r.stdout)
pl.write_text(json.dumps([{"id": "claude-master@claude-master-dev", "version": VER_CHECKOUT, "enabled": True, "noteDetails": "auto-update on"}]))
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b same version → PASS with the note", "PASS plugin_ok" in r.stdout and "auto-update on" in r.stdout, r.stdout)
pl.write_text(json.dumps([{"id": "claude-master@claude-master-dev", "version": VER_CHECKOUT, "enabled": True, "errorDetails": "hooks.json invalid"}]))
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b errorDetails → WARN quoting it", "WARN plugin_error" in r.stdout and "hooks.json invalid" in r.stdout, r.stdout)
pl.write_text("[]")
r = T.run_config(["doctor"], home, target, machine, extra_env={"CM_FAKE_PLUGIN_LIST": str(pl)})
T.check("C13b not installed → WARN with the install command", "WARN plugin_missing" in r.stdout and "claude plugin install" in r.stdout, r.stdout)

# C14 shim
r = T.run_config(["init", "--shim", "--yes"], home, target, machine)
shim = home / ".local" / "bin" / "claude-master"
T.check("C14 --shim installs ~/.local/bin/claude-master", shim.exists() and os.access(shim, os.X_OK), r.stdout + r.stderr)
conf_dir = Path(tmp) / "confdir"
(conf_dir / "plugins").mkdir(parents=True)
(conf_dir / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
    "claude-master@claude-master-dev": [{"scope": "user", "installPath": str(T.PLUGIN), "version": "0.1.0"}]}}))
env = {"PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_CONFIG_DIR": str(conf_dir)}
VER = json.loads((T.PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
b = subprocess.run([str(shim), "version"], capture_output=True, text=True, env=env)
T.check("C14 shim resolves root via installed_plugins.json", b.returncode == 0 and VER in b.stdout, b.stdout + b.stderr)
env2 = {"PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_MASTER_ROOT": str(T.PLUGIN)}
b = subprocess.run([str(shim), "version"], capture_output=True, text=True, env=env2)
T.check("C14 shim honours CLAUDE_MASTER_ROOT", b.returncode == 0 and VER in b.stdout, b.stdout + b.stderr)
env3 = {"PATH": os.environ["PATH"], "HOME": str(home), "CLAUDE_CONFIG_DIR": str(Path(tmp) / "empty")}
b = subprocess.run([str(shim), "version"], capture_output=True, text=True, env=env3)
T.check("C14 shim fails loudly when unresolved", b.returncode != 0 and "claude-master" in b.stderr, b.stdout + b.stderr)
b = subprocess.run([str(T.SCRIPTS / "claude-master"), "boh"], capture_output=True, text=True, env=env2)
T.check("C14 dispatcher: unknown subcommand → exit 2 with usage", b.returncode == 2 and "uso" in b.stderr.lower() or "usage" in b.stderr.lower(), b.stdout + b.stderr)

# C15 init --tmux
r = T.run_config(["init", "--tmux"], home, target, machine)
T.check("C15 --tmux prints tile/merge binds with the shim", r.returncode == 0 and "bind a run-shell -b \"$HOME/.local/bin/claude-master tile" in r.stdout and "bind u run-shell -b \"$HOME/.local/bin/claude-master merge" in r.stdout, r.stdout + r.stderr)
T.check("C15 --tmux prints the four arrows guarded by window_panes", r.stdout.count("if -F '#{==:#{window_panes},1}'") == 4 and "move left" in r.stdout and "select-pane -D" in r.stdout, r.stdout)
T.check("C15 --tmux prints, does not write", "claude-master" not in ((home / ".tmux.conf").read_text() if (home / ".tmux.conf").is_file() else ""), "")
noarrows = json.loads(target.read_text())
noarrows["tmux"]["keybindings"] = {"tile": "x", "merge": "", "move_arrows": False}
(Path(tmp) / "noarrows.json").write_text(json.dumps(noarrows))
r = T.run_config(["init", "--tmux"], home, Path(tmp) / "noarrows.json", machine)
T.check("C15 keybindings honoured (tile x, no merge, no arrows)", "bind x run-shell" in r.stdout and "merge" not in r.stdout and "select-pane" not in r.stdout, r.stdout)
r = T.run_config(["init", "--tmux", "--yes"], home, target, machine)
tc = (home / ".tmux.conf").read_text() if (home / ".tmux.conf").is_file() else ""
T.check("C15 --yes appends the block after the legacy one", r.returncode == 0 and tc.count("claude-master init --tmux") == 1 and tc.index("affianca") < tc.index("claude-master init --tmux"), r.stdout + r.stderr)
r = T.run_config(["init", "--tmux", "--yes"], home, target, machine)
T.check("C15 second --yes leaves one block", (home / ".tmux.conf").read_text().count("claude-master init --tmux") == 1 and r.returncode == 0, r.stdout)

T.rm(tmp)
T.rm(tmp2)
T.finish()
