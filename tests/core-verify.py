#!/usr/bin/env python3
"""Verifica cm-core.py: il cuore condiviso fra il relay del polso e gli avvisi.

Sono le funzioni PURE (niente rete, niente tmux): la lettura del transcript di Claude Code
(CO1-CO2), il tool in corso e la sua nota (CO3), e il testo degli avvisi (CO4-CO6). Nasce il
16/09/2026 dalle parti ancora vive di bot-ui-verify, quando il bot interattivo di Telegram e
cm-bot-ui.py sono stati ritirati.
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import cm_test as T  # noqa: E402

spec = importlib.util.spec_from_file_location("cm_core", T.SCRIPTS / "cm-core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

# CO1-CO2 transcript di Claude Code
tp = Path(tempfile.mkdtemp(prefix="cm-core-")) / "t.jsonl"
with open(tp, "w") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "fai X"}}) + "\n")
    f.write("riga rotta\n")
    f.write(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Cerco il codice…"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "git log --oneline", "description": "Storia"}}]}}) + "\n")
    f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}}) + "\n")
ev, off = core.transcript_events(str(tp), 0)
T.check("CO1 transcript events: user text, assistant text, tool_use (name + input); tool_result and broken lines ignored; offset = file size",
        [e[:2] for e in ev] == [("user", "fai X"), ("text", "Cerco il codice…"), ("tool", "Bash")] and off == tp.stat().st_size, str(ev))
ev2, off2 = core.transcript_events(str(tp), off)
T.check("CO2 from the offset: nothing new; a missing file gives ([], offset)",
        ev2 == [] and off2 == off and core.transcript_events(str(tp) + ".nope", 5) == ([], 5), str(ev2))

# CO3 tool in corso e nota
T.check("CO3 tool_line: tool and input whole, no cut mark; file_path for Read; plain text when no known key",
        core.tool_line("Bash", {"command": "git log --oneline"}) == "Bash git log --oneline"
        and core.tool_line("Read", {"file_path": "/a/b.py"}) == "Read /a/b.py"
        and core.tool_line("X", "y z") == "X y z", core.tool_line("Bash", {"command": "git log --oneline"}))
T.check("CO3 tool_note: the Bash description on one line, 120 chars; empty without it",
        core.tool_note({"command": "x", "description": "  Run  the suite\n"}) == "Run the suite"
        and core.tool_note({"command": "x"}) == "" and core.tool_note("x") == "", core.tool_note({"description": " a  b "}))

# CO4-CO6 testo degli avvisi
T.check("CO4 line: spaces normalized, no wrap and no cut (the bubble is as wide as its longest line)",
        core.line("  a   b\nc ") == "a b c", core.line("  a   b\nc "))
T.check("CO4 join: short lines joined with « · », empty parts dropped",
        core.join("a", "", "b") == "a · b" and core.join("", "") == "", core.join("a", "", "b"))
T.check("CO5 short_name: the account prefix stripped (longest first), cut at 14",
        core.short_name("work-ledger-api", ["work-"]) == "ledger-api"
        and core.short_name("a" * 20) == "a" * 14, core.short_name("work-ledger-api", ["work-"]))
long_q = "Il PC non accetta connessioni in entrata e l'orologio non ha un client. Da dove passano i comandi?"
T.check("CO6 question_gist: whole when it fits; else the last interrogative sentence that fits; else empty",
        core.question_gist("Vuoi A o B?") == "Vuoi A o B?"
        and core.question_gist(long_q) == "Da dove passano i comandi?"
        and core.question_gist("x" * 200) == "", core.question_gist(long_q))
# CO7 modello, effort e contesto dalla trascrizione (contratto 1.11)
proj = Path(tempfile.mkdtemp(prefix="cm-core-run-"))
conf = proj / "conf"
cwd = proj / "lavoro"
cwd.mkdir(parents=True)
import re as _re
slug = _re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve()))
tdir = conf / "projects" / slug
tdir.mkdir(parents=True)
tpath = tdir / "S-1.jsonl"


def scrivi(model_full, model_turn, effort, tokens):
    with open(tpath, "w") as f:
        f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
            "modelId": model_full, "marketingName": "Opus 5 (1M context)" if "[1m]" in model_full else "Sonnet 5"}}}) + "\n")
        f.write(json.dumps({"type": "assistant", "effort": effort, "message": {"model": model_turn, "usage": {
            "input_tokens": tokens[0], "cache_read_input_tokens": tokens[1], "cache_creation_input_tokens": tokens[2]}}}) + "\n")


core._CFG = {"accounts": {"personal": {"config_dir": str(conf)}}, "state_dir": str(proj), "relay": {"dir": str(proj / "relay")}, "bot": {}}
row = {"session_id": "S-1", "cwd": str(cwd), "account": "personal"}
scrivi("claude-opus-5[1m]", "claude-opus-5", "high", (100, 299_900, 0))
rt = core.session_runtime(row)
T.check("CO7 model with its window and label, effort, context over the 1M window (300k tokens → 30 %)",
        rt["model"] == {"id": "claude-opus-5[1m]", "label": "Opus 5"} and rt["effort"] == "high" and rt["context"] == 30, str(rt))
scrivi("claude-sonnet-5", "claude-sonnet-5", "medium", (0, 40_000, 10_000))
rt = core.session_runtime(row)
T.check("CO7 standard window: 50k of 200k → 25 %; the label comes from the system entry",
        rt["model"] == {"id": "claude-sonnet-5", "label": "Sonnet 5"} and rt["effort"] == "medium" and rt["context"] == 25, str(rt))
scrivi("claude-opus-5[1m]", "claude-sonnet-5", "high", (0, 100_000, 0))
rt = core.session_runtime(row)
T.check("CO7 the model changed mid-session (the two entries disagree): the id is kept, the window is not, so context is absent — never estimated",
        rt["model"] == {"id": "claude-sonnet-5", "label": None} and rt["context"] is None and rt["effort"] == "high", str(rt))
# CO8 (1.11.1, 16/09, dall'app: dal vivo label e context erano sempre nulli): la voce di sistema col modello sta
# all'AVVIO della conversazione, quindi fuori dalla coda in una sessione lunga. Si deve leggere anche la testa.
lungo = tdir / "S-2.jsonl"
with open(lungo, "w") as f:
    riempi = json.dumps({"type": "user", "message": {"role": "user", "content": "x" * 500}})
    # come dal vivo: la voce del modello non e' la prima riga — viene dopo prompt di sistema, memo e hook
    for _ in range(600):   # ~300 KB prima della voce, come nella sessione vera (221 KB)
        f.write(riempi + "\n")
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
        "modelId": "claude-opus-5[1m]", "marketingName": "Opus 5 (1M context)"}}}) + "\n")
    for _ in range(3000):   # oltre 1,5 MB dopo: la voce finisce fuori dalla coda letta
        f.write(riempi + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "high", "message": {"model": "claude-opus-5", "usage": {
        "input_tokens": 0, "cache_read_input_tokens": 500_000, "cache_creation_input_tokens": 0}}}) + "\n")
rt = core.session_runtime({"session_id": "S-2", "cwd": str(cwd), "account": "personal"})
T.check("CO8 long transcript, the entry 300 KB in (not the first line, as live): read from the head, label and the 1M window survive (500k → 50 %)",
        rt["model"] == {"id": "claude-opus-5[1m]", "label": "Opus 5"} and rt["context"] == 50 and rt["effort"] == "high", str(rt))
# e se il modello cambia a meta', la voce nuova sta nella CODA e deve vincere su quella della testa
with open(lungo, "a") as f:
    f.write(json.dumps({"type": "attachment", "attachment": {"type": "model", "identity": {
        "modelId": "claude-sonnet-5", "marketingName": "Sonnet 5"}}}) + "\n")
    f.write(json.dumps({"type": "assistant", "effort": "medium", "message": {"model": "claude-sonnet-5", "usage": {
        "input_tokens": 0, "cache_read_input_tokens": 50_000, "cache_creation_input_tokens": 0}}}) + "\n")
rt = core.session_runtime({"session_id": "S-2", "cwd": str(cwd), "account": "personal"})
T.check("CO8 the model changed mid-session: the entry in the tail wins over the one in the head (200k window → 25 %)",
        rt["model"] == {"id": "claude-sonnet-5", "label": "Sonnet 5"} and rt["context"] == 25, str(rt))
T.check("CO7 no transcript → the three fields are absent, without an error",
        core.session_runtime({"session_id": "nope", "cwd": str(cwd), "account": "personal"}) == {"model": None, "effort": None, "context": None}, "")
T.finish()
