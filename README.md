# cc-guard

Claude Code hook that keeps forbidden words, company names and files away from the model.
Python standard library only. Windows tested (Claude Code 2.1.322).

## What it does

| Event | Check | Result |
|---|---|---|
| `UserPromptSubmit` | forbidden words and regexes in the prompt | block (exit 2), reason without the value |
| `PreToolUse` | forbidden text in the tool input | block |
| `PreToolUse` | `Read` `Edit` `Write` `NotebookEdit` `Grep` `Glob` path must be inside `allow_paths` | block otherwise |
| `PreToolUse` | `Bash` `PowerShell` | denied by default (`"shell": "deny"`) |
| `PreToolUse` | any tool not in `allow_tools` | denied |

Any internal error (bad JSON, missing rules file) blocks the request (fail-closed).

## Why shells are denied

A shell command cannot be checked by parsing: `grep -r word *` names no forbidden path
but reads one (confirmed in a live session). The OS sandbox (`sandbox.filesystem`) is
not active on Windows ("feature gate off") and then silently does nothing, so it cannot
be relied on. Set `"shell": "allow"` only if you accept that gap.

## Rules file

`~/.cc-guard/rules.json` (Windows: `%USERPROFILE%\.cc-guard\rules.json`), or any path in `CC_GUARD_RULES`.
Never commit it (`.gitignore` already excludes `rules.json`). Start from `rules.example.json`.

| Key | Meaning |
|---|---|
| `words` | Forbidden words. Matched as a **substring** after lowercasing and `yo` -> `ye`, so a stem such as `company-stem` also catches every ending. Too short a word blocks harmless text (`bank` blocks `bankruptcy`). |
| `regexes` | Python regular expressions, case-insensitive, matched against the raw text. In JSON every backslash is doubled: `\d` is written `"\\d"`. |
| `allow_paths` | The only folders tools may touch. Absolute paths (forward slashes work on Windows) or relative to the project folder. Everything else is blocked. |
| `allow_tools` | Tools allowed besides the file tools. Default: `TodoWrite`, `Task`, `Agent`, `ToolSearch`. |
| `shell` | `"deny"` (default) blocks `Bash` and `PowerShell`; `"allow"` lets them through unchecked. |

## Regular expression examples

All examples are in `examples/regex-examples.json`; a unit test checks that each one matches its
sample and does not match its counter-sample. Put the ones you need into `regexes`.

| Purpose | Regex (as written in `rules.json`) |
|---|---|
| Company name with any ending | `"company-stem\\w*"` |
| Project code `PRJ-1234` | `"PRJ-\\d{3,5}"` |
| Corporate e-mail | `"[\\w.+-]+@corp\\.example\\.com"` |
| Internal host name | `"\\b[\\w-]+\\.int\\.example\\b"` |
| INN, 10 or 12 digits (no checksum, noisy) | `"\\b\\d{10}(\\d{2})?\\b"` |
| SNILS | `"\\b\\d{3}-\\d{3}-\\d{3}[ -]\\d{2}\\b"` |
| Russian mobile phone | `"(?:\\+7\|\\b8)[\\s(-]*\\d{3}[\\s)-]*\\d{3}[\\s-]*\\d{2}[\\s-]*\\d{2}\\b"` |
| Passport series and number (noisy) | `"\\b\\d{2}\\s?\\d{2}\\s?\\d{6}\\b"` |
| Bank card, 13-19 digits (noisy) | `"\\b(?:\\d[ -]?){13,19}\\b"` |
| IPv4 address | `"\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b"` |
| Private key header | `"-----BEGIN [A-Z ]*PRIVATE KEY-----"` |
| AWS access key id | `"AKIA[0-9A-Z]{16}"` |

(In the phone row the `\|` is only the Markdown table escape: write a plain `|` in the file.)
Digit-only patterns (INN, passport, card) also hit order numbers and timestamps. They have no
checksum validation; a wrong block costs only a resend, a missed leak costs more.

## Setup on a workstation

1. Copy `rules.example.json` to `~/.cc-guard/rules.json`, fill in your words, regexes and
   allowed folders.
2. Add the hooks from `settings.example.json` to `~/.claude/settings.json` with the real path
   to `guard.py`.
3. Restart Claude Code, run `/status`, then run the checks below.
4. Re-check after every Claude Code update that a blocked prompt does not stay in the model
   context (it did not on 2.1.322).

Environment: `CC_GUARD_RULES` rules path, `CC_GUARD_LOG` log path (default `~/.cc-guard/guard.log`).

## Running it on a VDI (Windows)

1. **Python.** `python --version` must print 3.8 or newer. The hook uses only the standard library.
2. **Get the code onto the VDI.** Clone this repository if the VDI can reach the Git host.
   If it cannot, bring a copy by whatever channel the VDI allows (archive, mail bundle).
   Put it in a stable folder, for example `C:\tools\cc-guard`.
3. **Create the rules file** on the VDI itself, `%USERPROFILE%\.cc-guard\rules.json`, from
   `rules.example.json`. Type the real company words there; do not bring them in through the
   repository. Set `allow_paths` to the project folders on the VDI.
4. **Register the hooks.**
   - Quick, per user: add the `hooks` block from `settings.example.json` to
     `%USERPROFILE%\.claude\settings.json`. The user can edit it.
   - Enforced, per machine (needs administrator rights): put the same `hooks` block plus
     `"allowManagedHooksOnly": true` into `C:\Program Files\ClaudeCode\managed-settings.json`.
     Claude Code does not read the older `C:\ProgramData\ClaudeCode` path. Make the file and
     the `guard.py` folder read-only for ordinary users, otherwise a user can edit the script.
     Drop-in files in `managed-settings.d\*.json` are merged in alphabetical order.
5. **Self-test without Claude** (the sample JSON is what Claude Code sends to the hook):

   ```
   set CC_GUARD_RULES=%USERPROFILE%\.cc-guard\rules.json
   echo {"hook_event_name":"UserPromptSubmit","prompt":"hello"} | python C:\tools\cc-guard\guard.py
   echo %ERRORLEVEL%
   ```
   Expected: `0` for a clean prompt, `2` plus `Blocked by policy: ...` when the prompt contains one
   of your words. Any other output means a setup problem (a missing rules file also gives `2`).
6. **Live test inside Claude Code.** Open a project inside an allowed folder and check:
   - a prompt containing one of your words is rejected;
   - asking Claude to read a file outside `allow_paths` is rejected;
   - asking Claude to run a shell command is rejected;
   - ask "what did I write in my previous message?" after a rejected prompt: the blocked text
     must not be known to the model.
7. **After every Claude Code update** repeat steps 5 and 6. Hook behaviour is not guaranteed
   across versions.
8. **Logs.** `%USERPROFILE%\.cc-guard\guard.log` records the reason of every block, never the
   matched value. If nothing is logged and nothing is blocked, the hook is not registered:
   run `/status` and check the `Setting sources` line.

Anything that goes wrong in the hook (unreadable rules, broken JSON) blocks the request instead
of letting it through, so a setup mistake shows up as "everything blocked", not as a leak.

## Tests

```
python -m unittest discover -s tests
```

## Known gaps

- Images, attachments and tool output are not inspected (needs an egress gateway).
- Allowed paths are checked by path only, not by file content.
- Word matching is a substring match on folded text; word forms are not stemmed.
