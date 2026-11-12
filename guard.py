#!/usr/bin/env python3
"""cc-guard: Claude Code hook that keeps forbidden text and files away from the model.

Events handled (one script, dispatch by hook_event_name):
  UserPromptSubmit  block a prompt containing a forbidden word or regex.
  PreToolUse        block a tool call that carries forbidden text or touches a path that is
                    outside the allowlist, inside a .git folder, or ignored by any .gitignore.

Rules file: $CC_GUARD_RULES or ~/.cc-guard/rules.json (kept OUTSIDE this repo).
See rules.example.json. Fail-closed: any error blocks the request (exit 2).
The block reason never repeats the matched value.

.gitignore handling uses `git check-ignore --no-index` against a throwaway bare git dir
with --work-tree set to a folder, so it works for folders that have a .gitignore but no
.git, and for repositories nested inside other repositories.
"""
import datetime
import json
import os
import re
import subprocess
import sys

DEFAULT_RULES = os.path.join(os.path.expanduser("~"), ".cc-guard", "rules.json")
LOG_PATH = os.path.join(os.path.expanduser("~"), ".cc-guard", "guard.log")
GIT_DIR = os.path.join(os.path.expanduser("~"), ".cc-guard", "gitdir")

FILE_TOOLS = {"Read", "Edit", "Write", "NotebookEdit", "MultiEdit"}
SEARCH_TOOLS = {"Grep", "Glob"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DEFAULT_ALLOW_TOOLS = ["TodoWrite", "Task", "Agent", "ToolSearch"]
GIT_TIMEOUT = 15


def load_rules(path=None):
    path = path or os.environ.get("CC_GUARD_RULES") or DEFAULT_RULES
    with open(path, encoding="utf-8") as f:
        rules = json.load(f)
    rules.setdefault("words", [])
    rules.setdefault("regexes", [])
    rules.setdefault("allow_paths", [])
    rules.setdefault("allow_tools", DEFAULT_ALLOW_TOOLS)  # list of names, or "*" for all
    rules.setdefault("shell", "deny")  # "deny" or "allow" (allow = unchecked)
    rules.setdefault("gitignore", True)  # forbid everything any .gitignore ignores
    rules.setdefault("block_git_dir", True)  # forbid everything inside a .git folder
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


# ---------------------------------------------------------------- paths
def resolve(path, cwd):
    return os.path.normcase(os.path.realpath(os.path.join(cwd, os.path.expanduser(path or ""))))


def allowed_root(resolved, cwd, rules):
    """Return the allowed root (resolved) that contains the path, or None."""
    for root in rules["allow_paths"]:
        root_abs = resolve(root, cwd)
        if resolved == root_abs or resolved.startswith(root_abs + os.sep):
            return root_abs
    return None


def has_dot_git(resolved):
    return any(part.lower() == ".git" for part in re.split(r"[\\/]", resolved))


def _ensure_git_dir():
    git_dir = os.environ.get("CC_GUARD_GITDIR") or GIT_DIR
    if not os.path.isfile(os.path.join(git_dir, "HEAD")):
        os.makedirs(git_dir, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-q", git_dir], check=True, timeout=GIT_TIMEOUT,
                       capture_output=True)
    return git_dir


def git_ignores(workdir, rel):
    """True if the .gitignore files seen from workdir ignore rel. Raises on any git error."""
    git_dir = _ensure_git_dir()
    proc = subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={workdir}", "-c", "core.excludesFile=",
         "check-ignore", "--no-index", "-q", "--", rel],
        capture_output=True, timeout=GIT_TIMEOUT,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise RuntimeError(f"git check-ignore failed with code {proc.returncode}")


def gitignored(resolved, root):
    """Check the path against the .gitignore chain: the allowed root and every folder above it
    that holds a .gitignore or .git. Ignored at any level means ignored."""
    levels = [root]
    parent = os.path.dirname(root)
    while parent and parent != os.path.dirname(parent):
        if os.path.exists(os.path.join(parent, ".gitignore")) or os.path.exists(os.path.join(parent, ".git")):
            levels.append(parent)
        parent = os.path.dirname(parent)
    for level in levels:
        if resolved == level:
            continue
        rel = os.path.relpath(resolved, level).replace("\\", "/")
        if git_ignores(level, rel):
            return True
    return False


def path_problem(path, cwd, rules):
    """Return a block reason for the path, or None if it may be used."""
    resolved = resolve(path, cwd)
    root = allowed_root(resolved, cwd, rules)
    if root is None:
        return "path not in allowlist"
    if rules["block_git_dir"] and has_dot_git(resolved):
        return "path is inside a .git folder"
    if rules["gitignore"] and gitignored(resolved, root):
        return "path is ignored by .gitignore"
    return None


def pattern_escapes(pattern):
    """A glob/pattern that is absolute or climbs up can point outside the allowed root."""
    if not pattern:
        return False
    p = pattern.replace("\\", "/")
    return p.startswith("/") or p.startswith("~") or ".." in p.split("/") or bool(re.match(r"^[A-Za-z]:", p))


# ---------------------------------------------------------------- checks
def check_prompt(prompt, rules):
    reason = check_text(prompt, rules)
    return f"prompt contains {reason}. Remove it and resend." if reason else None


def check_tool(tool, tool_input, cwd, rules):
    """Return a block reason or None. May raise on internal errors (caller fails closed)."""
    reason = check_text(json.dumps(tool_input, ensure_ascii=False), rules)
    if reason:
        return f"tool input contains {reason}"

    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        problem = path_problem(path, cwd, rules)
        return f"{problem} ({tool})" if problem else None

    if tool in SEARCH_TOOLS:
        problem = path_problem(tool_input.get("path") or cwd, cwd, rules)
        if problem:
            return f"search {problem} ({tool})"
        if pattern_escapes(tool_input.get("glob")) or (tool == "Glob" and pattern_escapes(tool_input.get("pattern"))):
            return f"glob escapes the allowlist ({tool})"
        return None

    if tool in SHELL_TOOLS:
        # Shell commands cannot be reliably checked by parsing (e.g. `grep -r x *`), and the OS
        # sandbox is unavailable on Windows. "allow" therefore means UNCHECKED.
        if rules["shell"] != "allow":
            return f"{tool} is not permitted"
        return None

    if rules["allow_tools"] == "*" or tool in rules["allow_tools"]:
        return None
    return f"tool {tool} is not permitted"


# ---------------------------------------------------------------- hook I/O
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
