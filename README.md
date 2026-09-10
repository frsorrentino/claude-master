# claude-master

![Version](https://img.shields.io/badge/version-0.3.1-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green) ![Status](https://img.shields.io/badge/status-beta-yellow)

**A Claude Code plugin for running many sessions on one machine.** Every session
lives in tmux, in the right folder and the right account, with a terminal window
whose tab tells you which one it is. From any of them (or from your phone) you
list the others, close them, restart one without losing its conversation, send a
prompt to a session of the other account and read its answer, forward a
screenshot to the right project, and get everything back after a reboot.

## Quickstart

```bash
claude plugin marketplace add frsorrentino/claude-master   # or the path of a checkout
claude plugin install claude-master@fsorrentino --scope user
claude-master init --dry-run        # every value read from this machine, with its source
claude-master init --yes --shim --shell   # config + ~/.local/bin/claude-master + shell integration
claude-master doctor                # PASS/WARN/FAIL with the fix for each line
claude-master launch ~/projects/x   # a session in tmux with its own terminal tab
```

Then `source ~/.config/claude-master/shell.sh` from your rc file (`init --shell`
prints the line), and `claude` typed in a terminal starts inside tmux with a
named, coloured tab.

## What it does

- **One session = one tmux session + one terminal tab.** The tab shape says which
  account (● personal, ■ the other), the colour says which session; the tmux
  name is the folder name (plus a per-account prefix). Remote Control gets the
  same name, so the session shows up in the app under the name you expect.
- **The account is deduced from the folder** (`folder_map`); a mismatch with the
  command you typed is a warning, never a ban.
- **`sessions`** lists every live session of every account: busy / idle / waiting
  on a question, whether someone is looking at it, how to reach it.
- **`talk`** sends a prompt to another session through its inbox socket and reads
  the reply from its transcript, across accounts; `wait` blocks until it is idle.
- **`restart arm`** restarts the current session at the end of the turn, resuming
  the conversation (`--clean` for a fresh context, `--switch-account` to continue
  on the other account).
- **`report`** archives a screenshot in the project and hands it to the project's
  session, launching it if needed: the phone-to-desk channel.
- **`restore`** relaunches, after a reboot, the sessions that were alive.
- **`tile` / `merge` / `move` / `layout`** arrange the session windows on the
  screen and the monitors (ChromeOS Terminal through chrome-bridge).
- **Hooks**: local time on every prompt, a short behavioural kernel at session
  start (the rules that matter more than the scripts), the peer registry kept
  fresh, an event ledger, prompts queued for a session's next Stop.

## Commands

| command | what it does | notes |
|---|---|---|
| `claude-master init [--dry-run\|--yes] [--force] [--shim] [--shell] [--cron] [--tmux]` | reads the machine and proposes (or writes) the configuration; the flags print (or install with `--yes`) the shim, the shell integration, the crontab line, the `.tmux.conf` block | every value comes with its source |
| `claude-master doctor` | PASS / WARN / FAIL with a remedy per line | exit 1 on a FAIL |
| `claude-master config [--sh\|--get KEY\|--path]` | the effective configuration | used by the scripts themselves |
| `claude-master launch <dir> [--create] [--continue\|--resume <id>] [--account N] [--no-window] [--bg] [--profile N]` | a session in tmux, dialogs answered, startup confirmed by the peer registry, window verified attached | `--bg` = `claude agents` session |
| `claude-master sessions [--watch]` | every live session, every account, with view and channel | |
| `claude-master close <name> \| --abandoned [--dry-run]` | closes a detached session; refuses one somebody is attached to | `--abandoned`: detached sessions stuck on a question |
| `claude-master restart arm [--clean\|--switch-account [N]]` | restart at the end of the turn, executed by the Stop hook | never kills a turn half-way |
| `claude-master talk <name> "prompt" [--wait S] [--quiet S] [--force] [--via socket\|tmux]` | prompt to another session, reply read from its transcript | needs `crossSessionInbound: accept` across accounts |
| `claude-master wait <name> [--timeout S]` | blocks until that session is idle | |
| `claude-master report <project> <image\|-> "text" [--no-launch]` | screenshot into `docs/<report.subdir>/`, prompt delivered to the project's session | |
| `claude-master queue <name> "prompt" [--expires M] \| --show \| --clear` | a prompt delivered at that session's next Stop | |
| `claude-master next [--all] [--attach]` | the session that needs you most (stuck and unseen first) | |
| `claude-master park <name> \| --idle-over D \| --ram-below MB [--auto]` | hibernate a session: folder, account and conversation recorded, then closed | `--ram-below` from cron with `--auto` |
| `claude-master unpark <name>` | bring a parked session back | |
| `claude-master registry [--show]` | reconcile the session registry (also from cron) | |
| `claude-master restore` | relaunch the sessions registered before a reboot | the shell integration proposes it after a fresh boot |
| `claude-master cloud <dir> "task" [--account N]` | a cloud session (`claude --cloud`) from the account of the folder | the folder needs a reachable GitHub remote |
| `claude-master follow <id\|url> "message"` | queue a message to a cloud session | |
| `claude-master desk [start\|stop\|status] [--no-window]` | a Remote Control desk in the workspace root: sessions opened on demand from the phone | optional, off by default (`desk.enabled`): the root session is the phone's entry point |
| `claude-master tile [names] [--rows\|--grid] [--on PLACE] [--dry-run] [--where]` | one window per session, side by side (grid when columns would be too narrow) | ChromeOS + chrome-bridge |
| `claude-master merge [names]` | every session as a tab of one Terminal window | |
| `claude-master move PLACE [names]` | to another monitor, and tile there | a monitor with no window on it is invisible to Chrome: drag one there first |
| `claude-master layout save\|restore\|list NAME` | named window layouts | |
| `claude-master attach <name> [ephemeral]` | attach the current terminal to a session | used by the tabs |
| `claude-master color <name>` | the tab shape and colour of a session | |
| `claude-master quota` | how full each account's quota is | reads fable-director's quota files if present |
| `claude-master diary [--date D\|--since H] [--send]` / `diary install\|uninstall\|status` | the day's diary from the hooks' ledger: per session start→end, turns, waits on a question, last assistant message; `--send` to Telegram, cron at `diary.cron_time` | |
| `claude-master night add <dir> "prompt" [--account N] [--model M] [--effort E] [--max-turns N]` / `list` / `remove <id>` / `run [--dry-run\|--one] [--send]` / `install\|uninstall\|status` | a queue of unattended jobs run with `claude -p` one at a time (cron at `night.cron_time`), guarded by free RAM and the account's five-hour quota; report in `<dir>/docs/notte/`, summary on Telegram | |
| `claude-master bot poll\|install\|uninstall\|status` | Telegram commands `/master`, `/launch <fragment>`, `/sessions` answered by a cron poller when no session is alive | off by default (`bot.enabled`), reuses the `telegram` plugin's bot |

`init --shell` also generates aliases in your language (`lancia`, `chiudi`,
`sessioni`, `affianca`… for Italian) from `shell.aliases`; every flag accepts
both spellings (`--crea`/`--create`, `--prova`/`--dry-run`, …).

Slash commands inside a session: `/claude-master:sessions`, `:launch`, `:close`,
`:restart`, `:report`, `:quota`. Skills: `claude-master:sessions` (the rules),
`claude-master:screen-layout`.

## From the phone when nothing is running

The `telegram` plugin of Claude Code lets you talk to a live session from your
phone. When every session is closed there is nobody to talk to: `claude-master
bot` fills that gap with a one-minute cron job that polls the same Telegram bot
(same token, same allowed chats as the plugin) and answers three commands only:

| command | what |
|---|---|
| `/master` | launches the root session (`workspace.root`) without a window and replies with its link |
| `/launch <fragment>` | resolves the folder like the skill does (one candidate → launches it; several or none → lists, never creates) |
| `/sessions` | the output of `claude-master sessions` |

Anything else from an allowed chat gets the list of commands; anything from a
chat not in `allowFrom` is ignored and logged (`<state_dir>/bot.log`). Telegram
delivers updates to **one** consumer per token, so the poller stays quiet while
a session's plugin is polling (its `bot.pid` is alive) and takes over only when
none is. On its first run it discards the backlog: yesterday's `/master` does
not launch anything today.

```bash
# config.json: "bot": {"enabled": true}
claude-master bot install    # * * * * * claude-master bot poll
claude-master bot status
```

Limits, stated plainly: after a **reboot** of the machine nobody is logged in
and cron does not run — the bot covers «sessions closed, machine awake», not
«machine off». On a Chromebook set the power settings so the machine does not
sleep while charging and closing the lid does not suspend it; a session left
alive (`--no-window`) keeps the plugin listening and is the surest bridge.

Every evening (`diary.cron_time`, 20:00) `claude-master diary --send` posts the
day's diary from the hooks' ledger to the same chats: which sessions ran, when,
how many turns, where they waited for you, what each one said last. Costs live
elsewhere (fable-director's receipts): the diary is *what happened*.

The night shift (`claude-master night`) is the third piece: queue jobs during
the day (`night add <dir> "prompt"`), and at `night.cron_time` they run one at
a time with `claude -p`, each in its folder and account, capped in turns and
time, only while free RAM and the account's five-hour quota allow; every job
leaves a report in the project's `docs/notte/` and the summary reaches the
same Telegram chats.

## Configuration

One file for the whole machine: `~/.config/claude-master/config.json`
(`CLAUDE_MASTER_CONFIG` overrides). The configuration describes the
workstation, not the login, so it serves every account. `claude-master init`
reads the machine (shell rc, settings of each account, live processes, legacy
scripts, tmux.conf, crontab) and proposes every value **with the fact it was
read from**; `--dry-run` shows, `--yes` writes, `--force` overwrites. The full
list of keys with defaults is `claude-master/config.example.json`.

| key | what |
|---|---|
| `accounts.<name>` | `config_dir` (`CLAUDE_CONFIG_DIR` of the account), `tmux_prefix`, `shape`, `shell_command`, `label` |
| `folder_map`, `workspace.root` | which folders belong to which account; the root maps to the `master` session |
| `session.link_wait_s` | seconds `launch` waits for the Remote Control link after the session registered (20; after a reboot six sessions start at once and the bridge is slower) |
| `session.claude_args` | flags passed to `claude` at launch; without `--dangerously-skip-permissions` a session starts in `dontAsk` mode and every Bash call is denied (init proposes it when your live sessions carry it) |
| `terminal.backend` | `chromeos`, `gnome`, `kitty`, `iterm2`, `macos-terminal`, `wt`, `none`, `auto` |
| `tabs.*` | title template, colour palette and registry |
| `shell.wrappers`, `shell.aliases`, `shell.restore_prompt` | which commands wrap `claude` per account, the aliases, the post-reboot prompt |
| `tmux.keybindings` | the keys of the `.tmux.conf` block printed by `init --tmux` (tile, merge, arrows = move) |
| `tile.*` | chrome-bridge CLI path, minimum column width, monitor names, placeholder and waits |
| `talk.*`, `report.*`, `restore.*`, `restart.*`, `registry.cron_minutes` | timeouts, subfolder for screenshots, uptime window, flag and log files, cron cadence |
| `hooks.*` | local time (format, prefix), Stop-hook restart, session kernel |
| `diary.*` | `cron_time` of the evening diary, `max_last_chars` |
| `night.*` | night shift: `cron_time`, `min_free_mb`, `max_quota_pct`, `item_timeout_s`, `max_turns`, `permission_mode`, `tool_memory_limit`, `out_subdir`, `max_items_per_run` |
| `bot.*` | Telegram poller: `enabled` (off), token/access/pid files of the `telegram` plugin, `cron_minutes`, `api_base` |
| `language` | `it` or `en` for every message |

State (session registry, colour registry, placeholder, restart flag, ledger,
layouts) lives under `state_dir` (default `~/.local/state/claude-master/`);
`init` keeps the existing files when it finds a legacy installation.

## Requirements

- tmux (tested with 3.3a), python3 ≥ 3.8, bash.
- Claude Code ≥ 2.1.263 (the peer registry `~/.claude/sessions/<pid>.json` and
  the inbox socket `talk` uses).
- `crossSessionInbound: accept` in the settings of an account that must receive
  `talk` from the other one.
- [chrome-bridge](https://github.com/frsorrentino/chrome-bridge) ≥ 1.16.1 only
  for `tile`, `merge`, `move` (one Claude session with its MCP running is
  enough); `layout` needs the `window_layout` CLI command, added after 1.16.1.

## Terminal backends

| backend | tried on hardware | notes |
|---|---|---|
| `chromeos` | **yes** (ChromeOS Terminal through garcon, daily use) | tabs, colours, `tile/merge/move/layout` |
| `gnome` | no | `gnome-terminal --tab`; written from the documentation, covered only by the tests with a fake backend |
| `kitty` | no | same |
| `iterm2` | no | `osascript`; same |
| `macos-terminal` | no | `osascript`; same |
| `wt` | no | Windows Terminal (`wt.exe`, WSL); same |
| `none` | yes | no window: sessions survive detached, `tmux attach -t =<name>` |

If you run one of the untried backends, the tests are a promise and the first
launch is the test: `launch` reports whether the window attached, and falls back
to a plain `tmux attach` line when it did not.

## How it works

- Every session is a tmux session whose name is the folder name. `launch` starts
  `claude --remote-control <name> -n <name>` inside it, answers the trust and
  permission dialogs, waits for the session's file in the official peer registry
  (`<config_dir>/sessions/<pid>.json`) and opens a terminal tab that attaches.
- `sessions` reads that registry for every account and reconciles it with
  `/proc` and tmux; `talk` writes into the session's inbox socket and reads the
  reply from its transcript.
- The plugin's hooks (SessionStart, SessionEnd, UserPromptSubmit,
  PermissionRequest, Stop, StopFailure) run from the plugin cache; the scripts run
  from wherever the shim `~/.local/bin/claude-master` resolves the current
  plugin version (or `plugin_root` in the config during development).
- Nothing is hardcoded to a machine: names, prefixes, folders, backends,
  monitors, language and keys are configuration, read once by `init`.

## Tests

```bash
for t in tests/*-verify.py; do python3 "$t"; done
```

Every suite prints `OK`/`FAIL` per case and exits non-zero on a failure. They
never touch your tmux server (a private one with `-L`), your Claude
(`tests/fixtures` has a fake `claude` that writes the registry files a real one
writes) or your browser (`tests/lib/fake-bridge.py` emulates Chrome's rules:
maximized and minimized windows ignore bounds, 50 % visibility, a duplicated tab
starts its shell only when activated). About 300 cases.

## License

MIT — Francesco Sorrentino.
