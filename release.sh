#!/usr/bin/env bash
# claude-master — release in one command (author tooling, not shipped in the zip).
#
#   bash release.sh 0.3.0            full release
#   bash release.sh 0.3.0 --check    preflight + test suites only, then stop (nothing published)
#
# Does, in order (fails loudly at the first broken step):
#   1. preflight   — version consistent in plugin.json / CHANGELOG / README badge, tag absent
#   2. tests       — every tests/*-verify.py must be green BEFORE the commit (house rule)
#   3. zip         — ../claude-master-plugin-<v>.zip
#   4. commit      — "release: <v>" on the local main, files staged BY NAME (never `git add -A`:
#                    other sessions work in this checkout). main is NEVER pushed: it carries docs/
#                    and BRIEF.md (this workstation's plans and notes).
#   4b. publish    — the same tree WITHOUT docs/ and BRIEF.md committed on the local branch
#                    `public` (its own history, first commit orphan) and pushed as origin/main
#   5. tag+release — v<v> on the PUBLIC commit, GitHub release with notes from CHANGELOG + zip
#   6. install     — copies the plugin into the cache of every local account that has it
#                    installed and updates installed_plugins.json (hooks run from the cache:
#                    T53, live sessions keep their old version until they restart)
set -euo pipefail

VER="${1:?usage: bash release.sh <version> [--check]  (e.g. 0.3.0)}"
CHECK=0; [ "${2:-}" = "--check" ] && CHECK=1
cd "$(dirname "$0")"
[ -f .claude-plugin/marketplace.json ] || { echo "FAIL: run from the repo root (marketplace.json missing)"; exit 1; }
PLUGIN=claude-master

echo "== 1/6 preflight =="
grep -q "\"version\": \"$VER\"" "$PLUGIN/.claude-plugin/plugin.json" \
  || { echo "FAIL: $PLUGIN/.claude-plugin/plugin.json is not at $VER — bump it first"; exit 1; }
grep -q "\*\*$VER" CHANGELOG.md \
  || { echo "FAIL: CHANGELOG.md has no **$VER entry"; exit 1; }
sed -n 3p README.md | grep -q "version-$VER-blue" \
  || { echo "FAIL: README version badge is not at $VER (line 3: shields.io version-<v>-blue)"; exit 1; }
git rev-parse "v$VER" >/dev/null 2>&1 \
  && { echo "FAIL: tag v$VER already exists"; exit 1; }
python3 tools-privacy-check.py --quiet \
  || { echo "FAIL: privacy check (docs/privacy-blocklist.txt) — neutralize before publishing"; exit 1; }
echo "preflight ok: $VER in plugin.json, CHANGELOG, README; tag v$VER free; privacy ok"

echo "== 2/6 test suites (must be green BEFORE the commit) =="
for t in tests/*-verify.py; do
  echo "-- $t"
  python3 "$t"
done

if [ "$CHECK" = 1 ]; then
  echo "CHECK OK: preflight and suites green, stopping before zip/commit/push"
  exit 0
fi

echo "== 3/6 zip =="
ZIP="../claude-master-plugin-$VER.zip"
rm -f "$ZIP"
( cd .. && zip -r -q "claude-master-plugin-$VER.zip" \
    claude-master/.claude-plugin \
    claude-master/claude-master \
    claude-master/README.md \
    claude-master/CHANGELOG.md \
    claude-master/LICENSE \
    -x '*/.git/*' '*/__pycache__/*' '*.bak*' '*.pyc' '*/evals/results/*' )
unzip -l "$ZIP" | tail -1

echo "== 4/6 commit on the local main (never pushed) =="
# Senza docs/ (14/09/2026: un'altra sessione vi lavora, e `git add docs` prendeva anche i suoi file non
# tracciati; docs/ non va comunque sul ramo pubblico: chi ci scrive lo committa da se'), e il commit SOLO
# su questi percorsi: quello che altri hanno in stage nel frattempo resta fuori.
REL_PATHS=(README.md CHANGELOG.md LICENSE release.sh cutover.sh .claude-plugin .gitignore "$PLUGIN" tests)
git add "${REL_PATHS[@]}"
if git diff --cached --quiet -- "${REL_PATHS[@]}"; then
  echo "(nothing new to commit — releasing HEAD as is)"
else
  git commit -m "release: $VER" -- "${REL_PATHS[@]}"
fi

echo "== 4b/6 public branch: the tree without docs/ and BRIEF.md → origin/main =="
git remote get-url origin >/dev/null 2>&1 || { echo "FAIL: no remote 'origin' (gh repo create ... && git remote add origin ...)"; exit 1; }
TMPIDX=$(mktemp)
GIT_INDEX_FILE="$TMPIDX" git read-tree HEAD
GIT_INDEX_FILE="$TMPIDX" git rm -q -r --cached docs BRIEF.md >/dev/null 2>&1 || true
PUB_TREE=$(GIT_INDEX_FILE="$TMPIDX" git write-tree)
rm -f "$TMPIDX"
PARENT=()
git rev-parse -q --verify refs/heads/public >/dev/null && PARENT=(-p "$(git rev-parse refs/heads/public)")
PUB=$(git commit-tree "$PUB_TREE" "${PARENT[@]}" -m "release: $VER")
git update-ref refs/heads/public "$PUB"
git push origin public:main
echo "public commit $PUB (tree without docs/ and BRIEF.md)"

echo "== 5/6 tag + GitHub release =="
TITLE=$(awk -v v="$VER" '$0 ~ "^- \\*\\*" v {sub(/^- \*\*/,""); sub(/\.\*\*.*/,""); sub(/^[0-9.]+ — /,""); print; exit}' CHANGELOG.md)
git tag -a "v$VER" -m "v$VER — ${TITLE:-release}" "$PUB"
git push origin "v$VER"
NOTES=$(mktemp)
awk -v v="$VER" '$0 ~ "^- \\*\\*" v {f=1} f {print} f && /^$/ {exit}' CHANGELOG.md > "$NOTES"
[ -s "$NOTES" ] || { echo "FAIL: could not extract $VER notes from CHANGELOG"; exit 1; }
gh release create "v$VER" --title "v$VER — ${TITLE:-release}" --notes-file "$NOTES" "$(readlink -f "$ZIP")"
rm -f "$NOTES"

echo "== 6/6 install into local accounts =="
SHA="$PUB"
python3 - "$VER" "$SHA" "$PLUGIN" <<'PY'
import json, os, re, shutil, sys
from datetime import datetime, timezone
VER, SHA, PLUGIN = sys.argv[1], sys.argv[2], sys.argv[3]
SRC = os.path.abspath(PLUGIN)
now = datetime.now(timezone.utc)
NOW = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
# Versions kept per account. NOT 1: a LIVE session still runs its hooks from the
# folder of its own version until it restarts (T53).
KEEP = 5
VERSION_DIR = re.compile(r"^\d+\.\d+\.\d+$")


def vkey(name):
    return tuple(int(x) for x in name.split("."))


def prune(root, keep_ver):
    """Keeps the KEEP newest versions plus the one just installed. Symlinks are
    aliases, not versions: unlinked only when dangling or when their target dies;
    never rmtree on a link. Every removal is verified afterwards."""
    if not os.path.isdir(root):
        return [], []
    entries = [d for d in os.listdir(root) if VERSION_DIR.match(d)]
    links = [d for d in entries if os.path.islink(f"{root}/{d}")]
    dirs = sorted((d for d in entries if not os.path.islink(f"{root}/{d}") and os.path.isdir(f"{root}/{d}")), key=vkey)
    keep = set(dirs[-KEEP:]) | {keep_ver}
    doomed = [v for v in dirs if v not in keep]
    for l in links:
        p = f"{root}/{l}"
        tgt = os.path.basename(os.readlink(p).rstrip("/"))
        if not os.path.exists(p) or tgt in doomed:
            doomed.append(l)
    removed, failed = [], []
    for v in doomed:
        p = f"{root}/{v}"
        try:
            if os.path.islink(p):
                os.unlink(p)
            else:
                shutil.rmtree(p)
        except OSError as e:
            failed.append(f"{v} ({e.__class__.__name__})")
            continue
        (removed if not os.path.lexists(p) else failed).append(v)
    return removed, failed


# Every CLAUDE_CONFIG_DIR of this machine: the default one plus the ones named in
# the claude-master config (accounts.<name>.config_dir), never hardcoded.
dirs = [os.path.expanduser("~/.claude")]
try:
    cfg = json.load(open(os.path.expanduser(os.environ.get("CLAUDE_MASTER_CONFIG") or "~/.config/claude-master/config.json")))
    for a in cfg.get("accounts", {}).values():
        d = os.path.expanduser(a.get("config_dir", ""))
        if d and d not in dirs:
            dirs.append(d)
except (OSError, ValueError):
    pass

for base in dirs:
    ipj = f"{base}/plugins/installed_plugins.json"
    if not os.path.exists(ipj):
        print(f"[skip] {base}: no installed_plugins.json"); continue
    d = json.load(open(ipj))
    key = next((k for k in d.get("plugins", {}) if k.startswith(f"{PLUGIN}@")), None)
    if not key:
        print(f"[skip] {base}: no {PLUGIN} entry in {ipj}"); continue
    mkt = key.split("@", 1)[1]
    cache = f"{base}/plugins/cache/{mkt}/{PLUGIN}/{VER}"
    if os.path.exists(cache):
        shutil.rmtree(cache)
    shutil.copytree(SRC, cache, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.bak*", "results"))
    shutil.copy2(ipj, ipj + f".bak-pre-{VER}")
    e = d["plugins"][key][0]
    e.update(installPath=cache, version=VER, gitCommitSha=SHA, lastUpdated=NOW)
    json.dump(d, open(ipj, "w"), indent=2)
    print(f"[installed] {base} ({key}) -> {VER} ({SHA[:7]})")
    dropped, failed = prune(f"{base}/plugins/cache/{mkt}/{PLUGIN}", VER)
    if dropped:
        print(f"[pruned]    {base} -> removed {len(dropped)} old versions, kept the last {KEEP}")
    if failed:
        print(f"[WARN]      {base} -> {len(failed)} entries NOT removed: {', '.join(failed)}")
PY

echo ""
echo "DONE v$VER — verify: https://github.com/frsorrentino/claude-master/releases/tag/v$VER"
echo "Local sessions pick up $VER on next restart (hooks run from the cache)."
echo "Marketplace fsorrentino entry: {\"source\": \"git-subdir\", \"url\": \"https://github.com/frsorrentino/claude-master.git\", \"path\": \"claude-master\", \"ref\": \"v$VER\", \"sha\": \"$PUB\"}"
echo "Public marketplace users: claude plugin marketplace update fsorrentino"
