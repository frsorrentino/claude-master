#!/usr/bin/env python3
"""Adattatori degli host di claude-master (piano docs/plans/2026-09-27-multi-pc.md, 2.1-2.2).

Un'interfaccia sola per ogni tipo di host. Il resto di claude-master (host, scheduler, offload, sondatore) usa solo
questi verbi e non sa se parla con la macchina locale, con un Linux via ssh o con un Windows nativo:

  probe(bench, webgl)   misure (1.2) in un dict
  exec(cmd, timeout)    comando breve e sincrono nella shell nativa: (rc, stdout, stderr)
  status()              snapshot: carico, RAM, disco, lavori, registro peer delle sessioni, file quota
  sync(job, build)      porta sull'host sorgente, asset mancanti e file di controllo; la cartella remota
  detach(job)           lancia il lavoro in modo che sopravviva al trasporto; id remoto
  fetch(job, paths, dest)  riporta i risultati, con lo sha256 remoto di ogni file
  cancel(job) · clean(job)

Il lavoro vero lo fa l'aiutante remoto (remote/cm-remote.sh o remote/cm-remote.ps1), versionato col plugin: stessi
verbi, stesso protocollo (una riga sentinella, poi UNA riga JSON sullo stdout). L'adattatore sceglie solo come
chiamarlo e come copiare i file.

Trasporto SSH: opzioni sempre da riga di comando, ~/.ssh/config non si tocca; known_hosts dedicato in state_dir con
StrictHostKeyChecking=yes (la chiave si fissa al primo `host add`, poi un cambio fallisce chiuso); connessione
riusata (ControlMaster) cosi' un sondaggio costa poco. Prove: CM_SSH_BIN e CM_SCP_BIN al posto di ssh e scp.
"""
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent
REMOTE = PLUGIN / "remote"
SENT = "@@CM-JSON@@"
HELPER_DIR = ".claude-master-remote"   # nella home remota
HELPER_FILES = {"posix": ["cm-remote.sh", "cm-bench.js", "cm-webgl.html"],
                "windows": ["cm-remote.ps1", "cm-bench.js", "cm-webgl.html"]}
KINDS = ("linux-tmux", "macos-tmux", "windows-native", "wsl", "cloud")
UNREACHABLE = re.compile(r"timed out|No route to host|Connection refused|Could not resolve|Network is unreachable|"
                         r"Connection closed by|Connection reset|kex_exchange_identification|Host is down", re.I)
HOSTKEY = re.compile(r"Host key verification failed|REMOTE HOST IDENTIFICATION HAS CHANGED|host key .* is not known", re.I)


class HostError(Exception):
    """Errore di un host, con un codice che i messaggi traducono: unreachable, hostkey, helper, remote, refused."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def local_helper_shas(family):
    return {f: sha256_file(REMOTE / f) for f in HELPER_FILES[family]}


def clixml_text(err):
    """Il testo leggibile di uno stderr CLIXML di PowerShell (i blocchi <S S="Error">, con _x000D__x000A_ a capo)."""
    if "#< CLIXML" not in err:
        return err.strip()
    parts = re.findall(r'<S S="Error">(.*?)</S>', err, re.S)
    t = "".join(parts).replace("_x000D__x000A_", "\n").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return t.strip()


def parse_helper(stdout):
    """Il JSON dopo la sentinella; None se manca (aiutante assente, shell rotta)."""
    if SENT not in stdout:
        return None
    tail = stdout.split(SENT, 1)[1].strip().splitlines()
    if not tail:
        return None
    try:
        return json.loads(tail[0])
    except ValueError:
        return None


# ------------------------------------------------------------------ trasporto
class SSH:
    def __init__(self, alias, state_dir, connect_timeout=4, first_contact=False):
        self.alias = alias
        self.state_dir = Path(state_dir)
        self.connect_timeout = int(connect_timeout)
        self.first_contact = first_contact
        self.ssh_bin = os.environ.get("CM_SSH_BIN") or "ssh"
        self.scp_bin = os.environ.get("CM_SCP_BIN") or "scp"

    def known_hosts(self):
        return self.state_dir / "known_hosts"

    def control_dir(self):
        """Il socket di ControlMaster deve stare sotto ~104 byte (limite dei socket Unix, macOS il piu' stretto): con
        uno state_dir lungo si ripiega su /tmp (spike del 27/09/2026: 'ControlPath too long')."""
        d = self.state_dir / "ssh"
        if len(str(d)) + 42 > 100:
            d = Path(tempfile.gettempdir()) / f"cm-ssh-{os.getuid()}"
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        return d

    def opts(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        return ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.connect_timeout}",
                "-o", "ControlMaster=auto", "-o", f"ControlPath={self.control_dir()}/%C", "-o", "ControlPersist=60",
                "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=2",
                "-o", f"UserKnownHostsFile={self.known_hosts()}", "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", "StrictHostKeyChecking=" + ("accept-new" if self.first_contact else "yes"),
                "-o", "LogLevel=ERROR"]

    def _check(self, rc, err):
        if rc == 255:
            if HOSTKEY.search(err):
                raise HostError("hostkey", err.strip().splitlines()[-1] if err.strip() else "")
            if UNREACHABLE.search(err) or not err.strip():
                raise HostError("unreachable", err.strip().splitlines()[-1] if err.strip() else "")

    def run(self, remote_cmd, timeout=60, input=None):
        argv = [self.ssh_bin] + self.opts() + [self.alias, remote_cmd]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input,
                               encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            raise HostError("unreachable", f"timeout {timeout}s")
        self._check(p.returncode, p.stderr)
        return p

    def put(self, local, remote, timeout=3600):
        argv = [self.scp_bin, "-q"] + self.opts() + [str(local), f"{self.alias}:{remote}"]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        self._check(p.returncode, p.stderr)
        if p.returncode != 0:
            raise HostError("remote", f"scp put {local}: {p.stderr.strip()}")

    def get(self, remote, local, timeout=3600):
        argv = [self.scp_bin, "-q"] + self.opts() + [f"{self.alias}:{remote}", str(local)]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        self._check(p.returncode, p.stderr)
        if p.returncode != 0:
            raise HostError("remote", f"scp get {remote}: {p.stderr.strip()}")

    def close(self):
        subprocess.run([self.ssh_bin] + self.opts() + ["-O", "exit", self.alias], capture_output=True, timeout=10)


# ------------------------------------------------------------------ adattatori
class Adapter:
    kind = ""
    family = "posix"
    remote = True
    verbs = {"probe", "exec", "status", "sync", "detach", "fetch", "cancel", "clean"}

    def __init__(self, name, host, cfg):
        self.name, self.host, self.cfg = name, host or {}, cfg
        sch = cfg.get("scheduler") or {}
        self.root = self.host.get("remote_root") or (sch.get("remote_root") or {}).get(self.family) or "~/claude-work"
        self.state_dir = Path(os.path.expanduser(cfg.get("state_dir") or "~/.local/state/claude-master"))
        if os.environ.get("CM_HOME") and str(cfg.get("state_dir", "")).startswith("~"):
            self.state_dir = Path(os.environ["CM_HOME"]) / str(cfg["state_dir"])[2:]
        self.timeout = int(sch.get("ssh_connect_timeout_s", 4))

    # --- da implementare per tipo
    def helper(self, verb, *args, timeout=120):
        raise NotImplementedError

    def put(self, local, remote_abs):
        raise NotImplementedError

    def get(self, remote_abs, local):
        raise NotImplementedError

    def exec(self, cmd, timeout=30):
        raise NotImplementedError

    def install_helper(self):
        raise NotImplementedError

    # --- verbi comuni, uguali per tutti (il protocollo dell'aiutante e' lo stesso)
    def call(self, verb, *args, timeout=120):
        d = self.helper(verb, *args, timeout=timeout)
        if not d.get("ok"):
            raise HostError("remote", d.get("error") or verb)
        return d

    def helper_version(self):
        try:
            return self.helper("version", timeout=30).get("files") or {}
        except HostError as e:
            if e.code in ("unreachable", "hostkey"):
                raise
            return {}

    def helper_ok(self):
        want = local_helper_shas(self.family)
        have = self.helper_version()
        return all(have.get(f) == s for f, s in want.items()), have

    def probe(self, bench=False, webgl=False):
        args = (["--bench"] if bench else []) + (["--webgl"] if webgl else [])
        return self.call("probe", *args, timeout=240 if (bench or webgl) else 90)

    def status(self):
        return self.call("status", timeout=60)

    def sync(self, job, build_bundle):
        """3.2: cartella del lavoro, manifesto degli asset, solo gli sha che mancano, un bundle, estrazione.
        `build_bundle(missing_shas)` prepara il bundle locale e ne restituisce il percorso (lo fa cm-offload)."""
        prep = self.call("prepare", job["id"])
        rdir, sep = prep["dir"], prep.get("sep") or "/"
        job["remote_dir"], job["remote_sep"] = rdir, sep
        missing = []
        if job.get("manifest"):
            self.put(job["manifest"], rdir + sep + "manifest.txt")
            missing = self.call("missing", job["id"]).get("missing") or []
        bundle = build_bundle(missing)
        self.put(bundle, rdir + sep + "bundle.tar")
        self.call("unpack", job["id"], timeout=1800)
        return {"dir": rdir, "missing": missing, "free_gb": prep.get("free_gb")}

    def detach(self, job):
        return self.call("detach", job["id"]).get("id")

    def fetch(self, job, paths, dest):
        d = self.call("pack", job["id"], *paths, timeout=1800)
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        tar_local = dest.parent / f".cm-out-{job['id']}.tar"
        self.get(d["tar"], tar_local)
        subprocess.run(["tar", "-xf", str(tar_local), "-C", str(dest)], check=True)
        tar_local.unlink()
        seen, out = set(), []
        for f in d.get("files") or []:   # un aiutante vecchio poteva elencare due volte lo stesso file
            if f["path"] not in seen:
                seen.add(f["path"])
                out.append(f)
        return out

    def cancel(self, job):
        return self.call("cancel", job["id"])

    def clean(self, job=None, older_days=None):
        if older_days is not None:
            return self.call("clean", "--older", str(older_days))
        return self.call("clean", job["id"])


class LocalPosix(Adapter):
    """L'host `local`: l'aiutante gira dalla copia del plugin, niente installazione e niente trasporto."""
    kind = "linux-tmux"
    remote = False

    def helper(self, verb, *args, timeout=120):
        try:
            p = subprocess.run(["sh", str(REMOTE / "cm-remote.sh"), self.root, verb, *args], capture_output=True,
                               text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise HostError("remote", f"{verb} timeout {timeout}s")
        d = parse_helper(p.stdout)
        if d is None:
            raise HostError("helper", p.stderr.strip()[-300:])
        return d

    def put(self, local, remote_abs):
        shutil.copyfile(local, remote_abs)

    def get(self, remote_abs, local):
        shutil.copyfile(remote_abs, local)

    def exec(self, cmd, timeout=30):
        p = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def install_helper(self):
        return True

    def helper_ok(self):
        return True, local_helper_shas("posix")


class SSHPosix(Adapter):
    """linux-tmux, macos-tmux e wsl raggiunti via ssh: l'aiutante e' cm-remote.sh in ~/.claude-master-remote."""
    kind = "linux-tmux"

    def __init__(self, name, host, cfg, first_contact=False):
        super().__init__(name, host, cfg)
        self.ssh = SSH((host.get("transport") or {}).get("ssh") or name, self.state_dir, self.timeout, first_contact)

    def helper(self, verb, *args, timeout=120):
        cmd = "sh ~/%s/cm-remote.sh %s" % (HELPER_DIR, " ".join(shlex.quote(a) for a in (self.root, verb, *args)))
        p = self.ssh.run(cmd, timeout=timeout)
        d = parse_helper(p.stdout)
        if d is None:
            raise HostError("helper", (p.stderr or p.stdout).strip()[-300:])
        return d

    def put(self, local, remote_abs):
        self.ssh.put(local, remote_abs)

    def get(self, remote_abs, local):
        self.ssh.get(remote_abs, local)

    def exec(self, cmd, timeout=30):
        p = self.ssh.run("sh -c " + shlex.quote(cmd), timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    def install_helper(self):
        rc, _, err = self.exec(f"mkdir -p ~/{HELPER_DIR}")
        if rc != 0:
            raise HostError("remote", err)
        for f in HELPER_FILES["posix"]:
            self.ssh.put(REMOTE / f, f"{HELPER_DIR}/{f}")
        return True


class MacOS(SSHPosix):
    kind = "macos-tmux"


class WSL(SSHPosix):
    kind = "wsl"


class WindowsNative(Adapter):
    """Windows nativo via OpenSSH: ogni chiamata e' `powershell -EncodedCommand` (base64 UTF-16: nessun problema di
    virgolette qualunque sia la shell di sshd, cmd o PowerShell); l'aiutante e' cm-remote.ps1 in
    %USERPROFILE%\\.claude-master-remote; i file solo con scp."""
    kind = "windows-native"
    family = "windows"

    def __init__(self, name, host, cfg, first_contact=False):
        super().__init__(name, host, cfg)
        self.ssh = SSH((host.get("transport") or {}).get("ssh") or name, self.state_dir, self.timeout, first_contact)

    @staticmethod
    def encoded(script):
        pre = "$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
        b = base64.b64encode((pre + script).encode("utf-16-le")).decode()
        return f"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {b}"

    @staticmethod
    def q(s):
        return "'" + str(s).replace("'", "''") + "'"

    def helper(self, verb, *args, timeout=120):
        script = "& (Join-Path $env:USERPROFILE %s) %s" % (self.q(HELPER_DIR + "\\cm-remote.ps1"),
                                                         " ".join(self.q(a) for a in (self.root, verb, *args)))
        p = self.ssh.run(self.encoded(script), timeout=timeout)
        d = parse_helper(p.stdout)
        if d is None:
            raise HostError("helper", clixml_text(p.stderr)[-300:] or p.stdout.strip()[-300:])
        return d

    @staticmethod
    def scp_path(p):
        return str(p).replace("\\", "/")

    def put(self, local, remote_abs):
        self.ssh.put(local, self.scp_path(remote_abs))

    def get(self, remote_abs, local):
        self.ssh.get(self.scp_path(remote_abs), local)

    def exec(self, cmd, timeout=30):
        p = self.ssh.run(self.encoded(cmd), timeout=timeout)
        return p.returncode, p.stdout, clixml_text(p.stderr)

    def install_helper(self):
        rc, _, err = self.exec(f"New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE '{HELPER_DIR}') | Out-Null")
        if rc != 0:
            raise HostError("remote", err)
        for f in HELPER_FILES["windows"]:
            self.ssh.put(REMOTE / f, f"{HELPER_DIR}/{f}")
        return True


class Cloud(Adapter):
    """Le sessioni cloud di Claude Code: niente exec ne' sync (ogni lavoro li' consumerebbe token). Le sessioni
    arrivano nella v2 (2.5); qui l'adattatore esiste perche' lo scheduler lo scarti con la sua regola."""
    kind = "cloud"
    verbs = {"launch_session", "post", "status"}

    def helper(self, verb, *args, timeout=120):
        raise HostError("refused", f"cloud: {verb}")

    def status(self):
        return {"ok": True, "at": int(time.time()), "jobs": {}, "sessions": [], "quota": []}


def adapter_for(name, host, cfg, first_contact=False):
    """L'adattatore di un host dichiarato (o di `local`)."""
    if name == "local":
        a = LocalPosix(name, host or {}, cfg)
        if os.uname().sysname == "Darwin":
            a.kind = "macos-tmux"
        return a
    kind = (host or {}).get("kind") or ""
    tr = (host or {}).get("transport") or {}
    if kind == "cloud" or tr.get("cloud"):
        return Cloud(name, host, cfg)
    cls = {"windows-native": WindowsNative, "macos-tmux": MacOS, "wsl": WSL}.get(kind, SSHPosix)
    return cls(name, host, cfg, first_contact=first_contact)


def detect_kind(name, host, cfg, first_contact=False):
    """Il tipo di un host ssh quando l'utente non l'ha dato: `uname -s` risponde su POSIX; su Windows risponde la
    PowerShell (o cmd) di sshd con un errore, e `$PSVersionTable` si'. Nessuna scrittura."""
    tmp = SSHPosix(name, host, cfg, first_contact=first_contact)
    p = tmp.ssh.run("uname -s 2>/dev/null || echo WINDOWS", timeout=30)
    out = (p.stdout or "").strip()
    if out.startswith("Linux"):
        p2 = tmp.ssh.run("grep -qi microsoft /proc/version && echo wsl || echo linux", timeout=30)
        return "wsl" if "wsl" in p2.stdout else "linux-tmux"
    if out.startswith("Darwin"):
        return "macos-tmux"
    p3 = tmp.ssh.run(WindowsNative.encoded("Write-Output ('CMWIN ' + $PSVersionTable.PSVersion.Major)"), timeout=30)
    if "CMWIN" in p3.stdout:
        return "windows-native"
    raise HostError("remote", f"unknown kind: {out[:80]} {clixml_text(p3.stderr)[:120]}")
