"""Adattatore finto per i test delle sessioni remote (CM_RSESSION_ADAPTER): l'host e' una cartella locale.

FAKE_WIN_ROOT    la cartella che fa da disco dell'host: il prefisso `W:` dei percorsi remoti diventa questa cartella
FAKE_WIN_NOREG=1 la sessione parte ma non si registra mai (Claude fermo su un dialogo)
Il registro peer finto sta in FAKE_WIN_ROOT/registry.json; ogni chiamata si annota in FAKE_WIN_ROOT/calls.jsonl.
talk: un messaggio rende la sessione busy; 1,5 s dopo la lettura successiva scrive nel transcript finto la risposta
«RISPOSTA: <testo>» e la rimette idle. FAKE_WIN_STATUS=nome:stato forza lo stato di una sessione."""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

_spec = importlib.util.spec_from_file_location("cm_adapters", Path(__file__).resolve().parents[2] / "team-supervisor" / "scripts" / "cm-adapters.py")
_ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ad)

ROOT = Path(os.environ["FAKE_WIN_ROOT"])


def local(rdir):
    return Path(str(rdir).replace("W:", str(ROOT), 1).replace("\\", "/"))


def note(verb, **kw):
    with open(ROOT / "calls.jsonl", "a") as f:
        f.write(json.dumps({"verb": verb, **kw}) + "\n")


def registry():
    p = ROOT / "registry.json"
    return json.loads(p.read_text()) if p.exists() else []


class FakeWin:
    def __init__(self, name, host, cfg):
        self.name = name

    def status(self):
        note("status")
        return {"ok": True, "sessions": [{"alive": not e.get("closed"), "entry": e} for e in registry()]}

    def session_prepare(self, rdir):
        note("prepare", rdir=rdir)
        d = local(rdir)
        m = d / ".cm-session.json"
        r = {"exists": d.exists(), "marker": m.exists()}
        if m.exists():
            j = json.loads(m.read_text())
            r.update(base=j["base"], root=j["root"], ahead=int(subprocess.run(["git", "-C", str(d), "rev-list", "--count", j["root"] + "..HEAD"],
                                                                                capture_output=True, text=True).stdout or 0))
        return r

    def session_seed(self, rdir, tar, sha, who):
        note("seed", rdir=rdir, sha=sha, who=list(who))
        d = local(rdir)
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-xf", str(tar), "-C", str(d)], check=True)
        g = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True, text=True).stdout  # noqa: E731
        g("init", "-q")
        for k, v in _ad.WindowsNative.SEED_GIT_CONFIG.items():   # la stessa config del seme vero
            g("config", k, v)
        g("config", "user.name", who[0] or "team-supervisor")
        g("config", "user.email", who[1] or "team-supervisor@localhost")
        with open(d / ".git" / "info" / "exclude", "a") as f:
            f.write(".cm-session.json\n")
        g("add", "-A")
        g("commit", "-q", "-m", f"team-supervisor: base {sha}")
        root = g("rev-parse", "HEAD").strip()
        (d / ".cm-session.json").write_text(json.dumps({"base": sha, "root": root}))
        return {"ok": True, "root": root}

    def session_trust(self, rdir):
        note("trust", rdir=rdir)
        return {"ok": True}

    def session_start(self, name, rdir, args):
        note("start", name=name, rdir=rdir, args=list(args))
        if not os.environ.get("FAKE_WIN_NOREG"):
            reg = registry()
            reg.append({"name": name, "sessionId": f"sid-{name}", "cwd": str(rdir), "bridgeSessionId": f"session_FAKE{len(reg)}"})
            (ROOT / "registry.json").write_text(json.dumps(reg))
        return {"ok": True}

    def session_patches(self, rdir, local_mbox):
        note("patches", rdir=rdir)
        d = local(rdir)
        root = json.loads((d / ".cm-session.json").read_text())["root"]
        n = int(subprocess.run(["git", "-C", str(d), "rev-list", "--count", root + "..HEAD"], capture_output=True, text=True).stdout)
        dirty = len([l for l in subprocess.run(["git", "-C", str(d), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines() if l.strip()])
        if n:
            with open(local_mbox, "wb") as f:
                subprocess.run(["git", "-C", str(d), "format-patch", "--stdout", root + "..HEAD"], stdout=f, check=True)
        return {"count": n, "dirty": dirty}

    # --- fase 2.2
    def _entry(self, name):
        for e in registry():
            if e["name"] == name:
                return e
        return None

    def _save(self, e):
        reg = [x if x["name"] != e["name"] else e for x in registry()]
        (ROOT / "registry.json").write_text(json.dumps(reg))

    def _tr(self, name):
        return ROOT / f"transcript-{name}.jsonl"

    def session_post(self, name, text, from_name, from_addr):
        note("post", name=name, text=text, from_name=from_name)
        e = self._entry(name)
        if not e or e.get("closed"):
            raise RuntimeError("not running")
        off = self._tr(name).stat().st_size if self._tr(name).exists() else 0
        e.update(status="busy", pending=text, posted=time.time())
        self._save(e)
        return {"ok": True, "offset": off, "status": "idle"}

    def session_read(self, name, offset, last=False):
        e = self._entry(name)
        if not e or e.get("closed"):
            return {"status": "gone", "alive": False, "texts": [], "offset": 0}
        if e.get("pending") is not None and time.time() - e["posted"] > 1.5:
            with open(self._tr(name), "a") as f:
                f.write(json.dumps({"type": "user", "message": {"content": e["pending"]}}) + "\n")
                f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "RISPOSTA: " + e["pending"]}]}}) + "\n")
            e.update(status="idle", pending=None)
            self._save(e)
        forced = dict(x.split(":", 1) for x in os.environ.get("FAKE_WIN_STATUS", "").split(",") if ":" in x)
        status = forced.get(name) or e.get("status") or "idle"
        data = self._tr(name).read_bytes() if self._tr(name).exists() else b""
        off = len(data) if offset == "end" else int(offset)
        texts = []
        for l in data[off:].decode().splitlines():
            d = json.loads(l)
            if d["type"] == "assistant":
                texts += [c["text"] for c in d["message"]["content"]]
        return {"status": status, "alive": True, "texts": texts[-1:] if last else texts, "offset": len(data)}

    def session_close(self, name, force=False):
        note("close", name=name, force=force)
        e = self._entry(name)
        if not e or e.get("closed"):
            return {"ok": True, "already": True}
        forced = dict(x.split(":", 1) for x in os.environ.get("FAKE_WIN_STATUS", "").split(",") if ":" in x)
        st = forced.get(name) or e.get("status") or "idle"
        if not force and st in ("busy", "waiting"):
            raise RuntimeError("remote: status " + st)
        e["closed"] = True
        self._save(e)
        return {"ok": True}

def adapter_for(name, host, cfg):
    return FakeWin(name, host, cfg)
