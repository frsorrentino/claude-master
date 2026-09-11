# Changelog

## 0.3.x

- **0.3.6 — 2026-09-11: the phone knows when a session stops.**
  - When a session stops on a question or a permission, the `PermissionRequest` hook sends the allowed Telegram chats the question, its numbered options as they appear on the session's screen and «reply “2 to NAME” to the master» (`hooks.ask_notify`, on by default, silent without the `telegram` plugin's token). Text, not an inline keyboard: a tap would reach the plugin's poller, not this one. `claude-master answer` gained the `--notify` entry the hook runs detached.
  - `claude-master screen <name> [--lines N]`: the last lines of a session's terminal, so «screen NAME» to the root session shows from the phone what a session is doing.
  - `recap`: closed projects under `recap.min_turns` (3) turns, or without a sentence, fold into one «other: a, b» line instead of one line each (the per-project `docs/recap.md` still gets its line).
  - `sessions`: NAME is the tmux name, the one every command accepts; `/rename` in the app only changes the peer registry's `name` (verified live), so a renamed session shows as `folder (app-name)` instead of a name `answer`/`talk`/`close` would not find.
  - Registry «last good set» (`registry.good_file`): the reconciled registry only refused to go EMPTY (T19), so four windows closed by hand before the reboot of 11/09 09:51 left one session in it and `restore` relaunched 1 of 5. The snapshot grows with the live set, is replaced only when every session in it is still alive, never shrinks on its own; a session leaves it only through an explicit close (`claude-master close`, `/exit` — `registry --closed NAME`). `restore` proposes the union of registry and snapshot and says where each session comes from, with the date (`registry --good` prints the snapshot).
  - Bot: `/start` answers «launch the root session?» with an inline button; the tap (a `callback_query`, which reaches this poller when no session is alive) launches the master like `/master`.
  - `merge`: the Terminal's start tab (`#home`) cannot be closed while other tabs exist (SWA behaviour, T78); a home-less app window only exists when a session tab was dragged out by hand, so `merge` prefers such a window as the collecting one and, when there is none, says so and uses the one with the start tab.
  - `restart arm`: one flag per session (`<flag_file>-<tmux>.json`); the Stop hook reads only its own. With a single file, eight `arm`s in two minutes (11/09 13:10) gave four restarts: every arm overwrote the previous one in silence. `restart list` (or `restart` alone) shows the armed sessions.
  - «Always tabs» (`terminal.open_as_tab`, on by default, ChromeOS): `launch`, `restart` and `restore` open the session as a TAB of a Terminal window already open (the merge mechanics: a shell tab duplicated, the placeholder consumed by the new shell), preferring a window without the start tab; garcon only when no Terminal window has a shell, and then ONE plain window whose shell becomes the source for the next sessions. `restore` runs one session at a time in that mode (~20 s each) and its dry run says so.
  - `tile` never detaches into popups any more (Franz: «always tabs, even side by side»): a session that shares a window, sits in a maximized/minimized one, or lives in a popup left by earlier versions, is reopened in an app window of its own through garcon (`attach NAME`, a new tmux client awaited, the old tab closed), then tiled.
  - No Terminal window shows the «Terminale» start tab next to a session any more (T80, chrome-bridge `ed24b83`): the start tab cannot be closed while other tabs exist, but it can be pulled out alone into a popup (`move_tab`) and closed there. `launch`/`restart`/`restore`, `tile` and `merge` evict it, by URL only and only when the window has other tabs. Verified live: `launch` of a test session lands as a tab of the working window in 16 s, windows 3→3, start tabs 0, popups 0.

- **0.3.5 — 2026-09-11: README opening names the master session.**

- **0.3.4 — 2026-09-10 (0.3.3 never left the machine).**
  - `doctor` checks the plugin in every account's cache through `claude plugin list --json` (Claude Code 2.1.268): version against the checkout, `errorDetails`/`noteDetails`, disabled or missing, with the fix.
  - Shell wrappers no longer depend on helper functions: Claude Code's shell snapshot captures the per-account wrappers but not the helpers, so `claude --version` inside a session printed «_cm_fn_exists: command not found» (T76).
  - `claude-master answer <name> --show | <n>`: read the question another session is stuck on and answer it by number through its terminal (a message in the inbox does not unblock a dialog); the phone answers through the root session.
  - `claude-master guard`: quota guard from cron — one Telegram warning per window above 95% with the reset time; at the reset, the sessions of that account that hit the wall (a failed turn in the ledger) get «resume where you were» through their inbox, and a pending night queue is run.
  - `claude-master recap`: the next step per project (same model call), under the open projects and in `docs/recap.md`.
  - Kernel rule 9: every turn ends with one line of outcome, never a bare «Fatto.» (feeds the recap, `next`, the registry).
  - A session starts knowing the last days: SessionStart prints the last `recap.startup_lines` (5) lines of the project's `docs/recap.md` after the kernel.
  - `diary` is now `recap` (`claude-master recap`, `/recap`; the old names stay as aliases, a `diary` block in config.json is read as `recap`): per project, one sentence on the whole day written by a cheap model from every end-of-turn message (cached per day), the real tab icons, the link on the name, no times or turns; the same line is appended to each project's `docs/recap.md` (`recap.project_log`, one line per date, rewritten if the day goes on).
  - `recap`: per project instead of per session (restarts merged, «restarted N×»), three groups in the order you need them (waiting on a question, alive, closed today; closed with zero turns hidden), a link to open every live project, the last message quoted only for the waiting ones and never a line with an email or a letter heading (`diary.last`: none | short | full); Telegram in HTML; `--full` keeps the old per-session list; `/recap` from the bot.

- **0.3.2 — 2026-09-10: the README, for people who have never seen it.**
  - README rewritten for readers who have never seen the plugin: who it is for, what you get with four illustrated cards (`assets/readme/`, generated by `tools-readme-cards.py`), what you are trusting, why not plain tmux, a minimal configuration; the command reference moved to the end. Reviewed by two other model families before landing.

- **0.3.1 — 2026-09-10: the first night with the legacy scripts gone.**
  - `launch` waits up to `session.link_wait_s` (10 s) for the Remote Control link: the peer registry file exists before the bridge is connected, and `--continue` reported «not yet in the registry» while the link was seconds away.
  - `restore` without a terminal stops at the list and asks for `--yes` (it used to relaunch every missing session when read from a Claude session).
  - `cloud` without a TTY runs `claude --cloud` in a detached tmux session with `remain-on-exit` and prints link and id (T75).
  - `cutover.sh` reads the crontab before writing it (the `-l | grep | -` pipeline raced on the spool).
  - `claude-master bot`: `/master`, `/launch <fragment>`, `/sessions` from Telegram when no session is alive — a cron poller on the `telegram` plugin's own bot (same token and allowed chats), quiet while a session's plugin is polling, backlog discarded on the first run, one update confirmed at a time, a lock against overlapping runs (phase 9, N2 reduced).
  - `claude-master recap`: the day's diary from the hooks' ledger (per session start→end or alive, turns, waits with tool and time, last assistant message, totals per account), `--send` to the allowed Telegram chats, `recap install` for the 20:00 cron (phase 9, N7).
  - `claude-master night`: a queue of unattended jobs run with `claude -p` one at a time from cron (guards on free RAM and five-hour quota, `--max-turns`, `--permission-mode`, `CLAUDE_CODE_TOOL_MEMORY_LIMIT`, per-item timeout), a report per job in the project's `docs/notte/`, the summary on Telegram (phase 9, N5).
  - `move`: the direction probe no longer lands on the built-in screen when the target monitor has no window (Chrome cannot see it: the message now says to drag one there); monitors measured within 40 px are the same monitor (no more `center1` ghosts); `layout restore`/`list` print a readable summary; `link_wait_s` default 20 s.
  - `desk` is optional and off by default (`desk.enabled`): sessions without a project, a random name and a fixed folder, plus a process always on — the root session stays the phone's single entry point.

- **0.3.0 — unreleased: shell, operator commands, restore, screen layout; first live-verified release (phases 5–7 + live tests).**
  - Shell integration generated by `init --shell`: wrappers per account (`shell.wrappers`), aliases in your language (`shell.aliases`), placeholder consumption for new tabs, post-reboot restore prompt; `init --cron` (registry reconciliation) and `init --tmux` (tile/merge/move keys from `tmux.keybindings`, appended once with `--yes`).
  - `next` (the session that needs you most), `park`/`unpark` (hibernate by name, by idle time or when RAM runs low), `queue` (a prompt for the next Stop), `cloud`/`follow`/`desk`.
  - `restore` after a reboot from the registry; `tile`/`merge`/`move`/`layout` for the ChromeOS Terminal through chrome-bridge, skill `screen-layout`.
  - Live tests on 2026-09-09 fixed four real defects: minimized windows are detached like maximized ones (T72); columns narrower than `tile.min_column_px` fall back to a grid; the duplicated tab is activated so the Terminal starts its shell (T73, merge of 6 sessions in 29 s instead of a timeout); `init` proposes `session.claude_args` from the live sessions, without which a launched session runs in `dontAsk` mode with every Bash denied (T74). chrome-bridge gained `window_layout` in its CLI for `layout`.
  - `release.sh`, README for the public marketplace listing.

## 0.2.x

- **0.2.0 — unreleased: every command of the legacy scripts, hooks, skill (phases 1–4).**
  - `sessions` from the official peer registry of every account (status busy/idle/waiting, tmux pane, Remote Control link, channel), reconciled with /proc; `color`, `attach`, terminal backends (chromeos, gnome, kitty, iterm2, macos-terminal, wt, none), `quota`.
  - `launch` (account from folder map, dialogs answered, startup confirmed by the registry, window verified attached, profiles, `--bg`), `registry`, `close`.
  - `talk` over the session's inbox socket with the reply read from the transcript (tmux typing as fallback), `wait`, `report`, `queue`.
  - Hooks: local time on every prompt, behavioural kernel at session start, registry refreshed on start/end, waiting flag from PermissionRequest, event ledger, armed `restart` executed by the Stop hook (`--clean`, `--switch-account`).
  - Skill `sessions` with the rules that matter more than the scripts; slash commands `sessions`, `quota`, `launch`, `close`, `restart`, `report`; `bin/claude-master` on the Bash tool PATH.

## 0.1.x

- **0.1.0 — unreleased: skeleton (phase 0).**
  - Plugin manifest, development marketplace, one configuration file with machine-wide defaults (`cm-config.py`), `claude-master init` that reads the machine and proposes every value with its source, `claude-master doctor`, message catalog (it/en), test harness with a private tmux server and live-captured fixtures.
