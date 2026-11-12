#!/usr/bin/env python3
"""cc-guard: Claude Code hook that keeps forbidden text and files away from the model.

Events handled (one script, dispatch by hook_event_name):
  UserPromptSubmit  block a prompt containing a forbidden word or regex.
  PreToolUse        block a tool call that carries forbidden text, touches a path
                    outside the allowlist, or uses a tool that is not permitted.

Rules file: $CC_GUARD_RULES or ~/.cc-guard/rules.json (kept OUTSIDE this repo).
See rules.example.json. Fail-closed: any error blocks the request (exit 2).
The block reason never repeats the matched value.
"""
import datetime
import json
import os
import re
import sys

DEFAULT_RULES = os.path.join(os.path.expanduser("~"), ".cc-guard", "rules.json")
LOG_PATH = os.path.join(os.path.expanduser("~"), ".cc-guard", "guard.log")

FILE_TOOLS = {"Read", "Edit", "Write", "NotebookEdit", "MultiEdit"}
SEARCH_TOOLS = {"Grep", "Glob"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DEFAULT_ALLOW_TOOLS = ["TodoWrite", "Task", "Agent", "ToolSearch"]


def load_rules(path=None):
    path = path or os.environ.get("CC_GUARD_RULES") or DEFAULT_RULES
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    rules.setdefault("words", [])
    rules.setdefault("regexes", [])
    rules.setdefault("allow_paths", [])
    rules.setdefault("allow_tools", DEFAULT_ALLOW_TOOLS)
    rules.setdefault("shell", "deny")  # "deny" or "allow"
    return rules


def norm(text):
    """Lowercase and fold the Russian yo so matching is case/yo insensitive."""
    return text.lower().replace("ё", "е")


def check_text(text, rules):
    """Return a reason (without the matched value) or None."""
    folded = norm(text)
    for i, word in enumerate(rules["words"]):
        if norm(word) in folded:
            return f"forbidden word #{i}"
    for i, rx in enumerate(rules["regexes"]):
        if re.search(rx, text, re.IGNORECASE):
            return f"forbidden pattern #{i}"
    return None


def path_allowed(path, cwd, rules):
    """True if the resolved path lies inside one of the allowed roots."""
    base = os.path.expanduser(path or "")
    resolved = os.path.normcase(os.path.realpath(os.path.join(cwd, base)))
    for root in rules["allow_paths"]:
        root_abs = os.path.normcase(os.path.realpath(os.path.join(cwd, os.path.expanduser(root))))
        if resolved == root_abs or resolved.startswith(root_abs + os.sep):
            return True
    return False


def pattern_escapes(pattern):
    """A glob/pattern that is absolute or climbs up can point outside the allowed root."""
    if not pattern:
        return False
    p = pattern.replace("\\", "/")
    return p.startswith("/") or p.startswith("~") or ".." in p.split("/") or bool(re.match(r"^[A-Za-z]:", p))


def check_prompt(prompt, rules):
    reason = check_text(prompt, rules)
    return f"prompt contains {reason}. Remove it and resend." if reason else None


def check_tool(tool, tool_input, cwd, rules):
    """Return a block reason or None."""
    reason = check_text(json.dumps(tool_input, ensure_ascii=False), rules)
    if reason:
        return f"tool input contains {reason}"

    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not path_allowed(path, cwd, rules):
            return f"path not in allowlist ({tool})"
        return None

    if tool in SEARCH_TOOLS:
        if not path_allowed(tool_input.get("path") or cwd, cwd, rules):
            return f"search path not in allowlist ({tool})"
        if pattern_escapes(tool_input.get("glob")) or (tool == "Glob" and pattern_escapes(tool_input.get("pattern"))):
            return f"glob escapes the allowlist ({tool})"
        return None

    if tool in SHELL_TOOLS:
        # Shell commands cannot be reliably checked by parsing (e.g. `grep -r x *`),
        # and the OS sandbox is unavailable on Windows, so shells are denied by default.
        if rules["shell"] != "allow":
            return f"{tool} is not permitted"
        return None

    if tool in rules["allow_tools"]:
        return None
    return f"tool {tool} is not permitted"


def log(msg):
    path = os.environ.get("CC_GUARD_LOG") or LOG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")
    except OSError:
        pass


def block(reason):
    log(f"BLOCK {reason}")
    sys.stderr.write(f"Blocked by policy: {reason}\n")
    sys.exit(2)


def main():
    rules = load_rules()
    # Decode stdin as UTF-8 explicitly: the Windows locale codec would mangle Cyrillic.
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    event = data.get("hook_event_name")
    cwd = data.get("cwd") or os.getcwd()

    if event == "UserPromptSubmit":
        reason = check_prompt(data.get("prompt", ""), rules)
    elif event == "PreToolUse":
        reason = check_tool(data.get("tool_name", ""), data.get("tool_input", {}), cwd, rules)
    else:
        reason = f"unexpected event {event}"
    if reason:
        block(reason)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail-closed
        log(f"ERROR {exc!r}")
        sys.stderr.write("Guard error, request blocked (fail-closed).\n")
        sys.exit(2)
