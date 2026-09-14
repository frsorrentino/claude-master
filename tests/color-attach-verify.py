#!/usr/bin/env python3
"""Verifica cm-color.sh e cm-attach.sh su un tmux privato.

K1  forma dall'account (prefisso): nome senza prefisso → tondo, `pix-` → quadrato; etichetta senza prefisso
K2  colori diversi per sessioni vive diverse; stabile: la stessa sessione riprende il suo colore (T25)
K3  le voci di sessioni morte vengono potate; il colore liberato torna disponibile
K4  oltre 7 vive: si riusa la coppia forma+colore ancora libera (🟦 e 🔵 restano distinti)
K5  concorrenza: due chiamate parallele per due sessioni nuove → colori diversi (T56)
K6  registro nel formato legacy `nome<TAB>indice` (D4: condiviso con colore-sessione)
K7  CM_* ereditate da un'ALTRA config (14/09: dal server tmux della macchina) non si impongono: cm-color scrive
    nel registro della config indicata, non tocca l'altro; una config modificata si ricarica
A1  attach ephemeral: dopo l'attacco destroy-unattached e' `on` e set-titles-string e' il titolo (T7, T26)
A2  attach senza modo: destroy-unattached resta `off`
A3  attach su sessione inesistente → exit 3 con l'elenco
A4  gli argomenti di attach non iniziano mai con `-` (T6): `attach NOME ephemeral`
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

tmp = T.tmpdir()
home = Path(tmp) / "home"
(home / ".claude").mkdir(parents=True)
(home / ".claude-pixel").mkdir(parents=True)
reg = Path(tmp) / "colors"
cfg = Path(tmp) / "config.json"
cfg.write_text(json.dumps({
    "language": "it",
    "accounts": {"personale": {"config_dir": str(home / ".claude")},
                 "professionale": {"config_dir": str(home / ".claude-pixel"), "tmux_prefix": "pix-"}},
    "default_account": "personale",
    "tabs": {"color_registry": str(reg)},
}))


def run(script, *args, extra=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}
    env.pop("TMUX", None)
    if extra:
        env.update(extra)
    return subprocess.run([str(T.SCRIPTS / script)] + list(args), capture_output=True, text=True, env=env, timeout=60)


def color(name):
    return run("cm-color.sh", name).stdout.strip()


with T.PrivateTmux() as tm:
    for s in ("alfa", "pix-beta", "gamma"):
        tm("new-session", "-d", "-s", s, "bash", "--norc")
    c_alfa, c_beta, c_gamma = color("alfa"), color("pix-beta"), color("gamma")
    T.check("K1 personale → tondo", c_alfa.split()[0] in "🔴🟠🟡🟢🔵🟣⚪" and c_alfa.endswith(" alfa"), c_alfa)
    T.check("K1 professionale → quadrato, etichetta senza prefisso", c_beta.split()[0] in "🟥🟧🟨🟩🟦🟪⬜" and c_beta.endswith(" beta"), c_beta)
    T.check("K2 three live sessions, three different colour indexes",
            len({reg.read_text().split("\n")[i].split("\t")[1] for i in range(3)}) == 3, reg.read_text())
    T.check("K2 stable on re-ask", color("alfa") == c_alfa and color("pix-beta") == c_beta, color("alfa"))
    T.check("K6 legacy registry format name<TAB>index", all(len(l.split("\t")) == 2 for l in reg.read_text().strip().split("\n")), reg.read_text())

    # K3: gamma muore, il suo colore torna libero per delta
    tm("kill-session", "-t", "=gamma")
    tm("new-session", "-d", "-s", "delta", "bash", "--norc")
    c_delta = color("delta")
    T.check("K3 dead session pruned from registry", "gamma" not in reg.read_text(), reg.read_text())
    T.check("K3 freed colour reused", c_delta.split()[0] == c_gamma.split()[0], f"{c_gamma} vs {c_delta}")

    # K4: oltre 7 vive nella stessa forma → coppia forma+colore
    for i in range(8):
        tm("new-session", "-d", "-s", f"s{i}", "bash", "--norc")
    cols = [color(f"s{i}") for i in range(8)]
    idx = [l.split("\t") for l in reg.read_text().strip().split("\n")]
    used = {n: int(i) for n, i in idx}
    T.check("K4 with more than 7 live, an index repeats", len({used[f"s{i}"] for i in range(8)} | {used['alfa'], used['delta']}) <= 7, str(used))
    c_pix2 = color("pix-nuova")
    tondo_idx = {used[n] for n in used if not n.startswith("pix-")}
    quad_idx = {used[n] for n in used if n.startswith("pix-")}
    used2 = {l.split("\t")[0]: int(l.split("\t")[1]) for l in reg.read_text().strip().split("\n")}
    T.check("K4 square picks a colour whose square pair is free",
            used2["pix-nuova"] not in quad_idx, f"{c_pix2} idx={used2['pix-nuova']} quad_used={quad_idx}")

    # K5: concorrenza
    tm("new-session", "-d", "-s", "par1", "bash", "--norc")
    tm("new-session", "-d", "-s", "par2", "bash", "--norc")
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(cfg),
           "CM_TMUX_ARGS": tm.env["CM_TMUX_ARGS"]}
    reg.write_text("")   # tutti liberi: senza lock i due sceglierebbero lo stesso 0
    ps = [subprocess.Popen([str(T.SCRIPTS / "cm-color.sh"), n], stdout=subprocess.PIPE, text=True, env=env) for n in ("par1", "par2")]
    outs = [p.communicate(timeout=30)[0].strip() for p in ps]
    T.check("K5 parallel calls get different colours (mkdir lock)", outs[0].split()[0] != outs[1].split()[0], str(outs))

    # A1: attach ephemeral dentro un tmux "runner" (serve un tty) — l'attach usa il socket privato
    tm("new-session", "-d", "-s", "target", "bash", "--norc")
    runner_cmd = (f"env -u TMUX PATH='{os.environ['PATH']}' HOME='{home}' CM_HOME='{home}' CLAUDE_MASTER_CONFIG='{cfg}' "
                  f"CM_TMUX_ARGS='{tm.env['CM_TMUX_ARGS']}' '{T.SCRIPTS}/cm-attach.sh' target ephemeral; sleep 5")
    tm("new-session", "-d", "-s", "runner", "-x", "80", "-y", "24", runner_cmd)
    for _ in range(20):
        time.sleep(0.5)
        if "on" in (tm("show-options", "-t", "target", "-v", "destroy-unattached").stdout or ""):
            break
    T.check("A1 destroy-unattached on after ephemeral attach", "on" in tm("show-options", "-t", "target", "-v", "destroy-unattached").stdout,
            tm("show-options", "-t", "target").stdout)
    T.check("A1 set-titles-string = colour title", "target" in tm("show-options", "-t", "target", "-v", "set-titles-string").stdout,
            tm("show-options", "-t", "target").stdout)
    T.check("A1 target attached by the runner", "1" in tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout.split("target ")[1][:1],
            tm("list-sessions", "-F", "#{session_name} #{session_attached}").stdout)
    tm("kill-session", "-t", "=runner")
    # A2
    tm("new-session", "-d", "-s", "plain", "bash", "--norc")
    runner_cmd2 = runner_cmd.replace("target ephemeral", "plain")
    tm("new-session", "-d", "-s", "runner2", "-x", "80", "-y", "24", runner_cmd2)
    time.sleep(2)
    T.check("A2 plain attach leaves destroy-unattached off", "on" not in (tm("show-options", "-t", "plain", "-v", "destroy-unattached").stdout or ""),
            tm("show-options", "-t", "plain").stdout)
    tm("kill-session", "-t", "=runner2")
    # A3
    r = run("cm-attach.sh", "nessuna")
    T.check("A3 missing session → exit 3 with list", r.returncode == 3 and "alfa" in r.stderr, r.stderr)
    # K7: le CM_* di una sessione nata sotto un'altra config (qui: il «registro vero» è colors-altra)
    def loaded_env(config):
        out = subprocess.run(["bash", "-c", f"source '{T.SCRIPTS / 'cm-lib.sh'}' && env -0"], capture_output=True,
                             env={"PATH": os.environ["PATH"], "HOME": str(home), "CM_HOME": str(home), "CLAUDE_MASTER_CONFIG": str(config)}).stdout
        return {k: v for k, v in (kv.split("=", 1) for kv in out.decode().split("\0") if kv.startswith("CM_") and "=" in kv) if k != "CM_TMUX_ARGS"}
    other_reg = Path(tmp) / "colors-altra"; other_reg.write_text("vera\t0\n")
    other_cfg = Path(tmp) / "config-altra.json"
    other_cfg.write_text(json.dumps({"language": "it", "tabs": {"color_registry": str(other_reg)}}))
    r = run("cm-color.sh", "k7", extra=loaded_env(other_cfg))
    T.check("K7 CM_* inherited from another config don't win: the given config's registry gets the entry, the other is untouched (no pruning of live sessions)",
            other_reg.read_text() == "vera\t0\n" and "k7\t" in reg.read_text(), r.stdout + r.stderr + other_reg.read_text())
    inherited = loaded_env(cfg)
    reg2 = Path(tmp) / "colors-2"
    saved = cfg.read_text()
    cfg.write_text(saved.replace(json.dumps(str(reg)), json.dumps(str(reg2))))
    os.utime(cfg, (time.time() + 5, time.time() + 5))
    r = run("cm-color.sh", "k7b", extra=inherited)
    T.check("K7 same config file modified after the load → reloaded: the new registry path is used", reg2.exists() and "k7b\t" in reg2.read_text(), r.stdout + r.stderr)
    cfg.write_text(saved)
    # A4
    src = (T.SCRIPTS / "cm-terminal.sh").read_text()
    T.check("A4 terminal backends call `attach NAME ephemeral` (no leading dash)", 'attach "$nome"' in src and "attach -t" not in src.split("chromeos)")[1].split("gnome)")[0], "")

T.rm(tmp)
T.finish()
