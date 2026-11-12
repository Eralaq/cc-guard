import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import guard  # noqa: E402

HAVE_GIT = shutil.which("git") is not None


def write(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def git_init(path):
    subprocess.run(["git", "init", "-q", path], check=True, capture_output=True)


@unittest.skipUnless(HAVE_GIT, "git is required")
class GitignoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ccg-"))
        os.environ["CC_GUARD_GITDIR"] = os.path.join(cls.tmp, "_gd")
        code = os.path.join(cls.tmp, "Code")
        cls.code = code
        # Code/ is not a repository; it holds a .gitignore, several repos and a fake folder
        write(os.path.join(code, ".gitignore"), "topsecret.txt\nrepoA/hidden/\n")
        write(os.path.join(code, "topsecret.txt"))
        write(os.path.join(code, "plain.txt"))
        write(os.path.join(code, "repoA", ".gitignore"), "*.env\nignored-sub/\n")
        write(os.path.join(code, "repoA", "a.env"))
        write(os.path.join(code, "repoA", "ok.txt"))
        write(os.path.join(code, "repoA", "hidden", "h.txt"))
        write(os.path.join(code, "repoA", "ignored-sub", "f.txt"))
        write(os.path.join(code, "repoA", "inner", ".gitignore"), "private/\n!keep.txt\n")
        write(os.path.join(code, "repoA", "inner", "private", "p.txt"))
        write(os.path.join(code, "repoA", "inner", "ok.txt"))
        write(os.path.join(code, "fake", ".gitignore"), "x/\n")  # .gitignore without .git
        write(os.path.join(code, "fake", "x", "y.txt"))
        write(os.path.join(code, "fake", "z.txt"))
        git_init(os.path.join(code, "repoA"))
        git_init(os.path.join(code, "repoA", "inner"))
        git_init(os.path.join(code, "repoB"))
        write(os.path.join(code, "repoB", "b.txt"))
        cls.rules = {
            "words": [], "regexes": [], "allow_paths": [code], "allow_tools": "*", "shell": "deny",
            "gitignore": True, "block_git_dir": True,
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        os.environ.pop("CC_GUARD_GITDIR", None)

    def read(self, *parts, rules=None):
        path = os.path.join(self.code, *parts)
        return guard.check_tool("Read", {"file_path": path}, self.code, rules or self.rules)

    def test_open_files_pass(self):
        for parts in (("plain.txt",), ("repoA", "ok.txt"), ("repoA", "inner", "ok.txt"),
                      ("fake", "z.txt"), ("repoB", "b.txt")):
            with self.subTest(parts):
                self.assertIsNone(self.read(*parts))

    def test_gitignore_file_itself_is_readable(self):
        self.assertIsNone(self.read(".gitignore"))
        self.assertIsNone(self.read("repoA", ".gitignore"))

    def test_ignored_by_folder_without_repo(self):
        self.assertIsNotNone(self.read("topsecret.txt"))

    def test_ignored_by_pattern_in_repo(self):
        self.assertIsNotNone(self.read("repoA", "a.env"))

    def test_ignored_parent_directory(self):
        self.assertIsNotNone(self.read("repoA", "ignored-sub", "f.txt"))

    def test_ignored_in_nested_repo(self):
        self.assertIsNotNone(self.read("repoA", "inner", "private", "p.txt"))

    def test_gitignore_without_git_folder(self):
        self.assertIsNotNone(self.read("fake", "x", "y.txt"))

    def test_rule_in_ancestor_above_allowed_root(self):
        # allowed root is repoA, but Code/.gitignore (above it) ignores repoA/hidden/
        rules = dict(self.rules, allow_paths=[os.path.join(self.code, "repoA")])
        self.assertIsNotNone(self.read("repoA", "hidden", "h.txt", rules=rules))
        self.assertIsNone(self.read("repoA", "ok.txt", rules=rules))

    def test_dot_git_forbidden(self):
        self.assertIsNotNone(self.read("repoA", ".git", "config"))
        self.assertIsNotNone(self.read("repoA", "inner", ".git", "HEAD"))

    def test_dot_git_switch_off(self):
        rules = dict(self.rules, block_git_dir=False)
        self.assertIsNone(self.read("repoB", ".git", "HEAD", rules=rules))

    def test_gitignore_switch_off(self):
        rules = dict(self.rules, gitignore=False)
        self.assertIsNone(self.read("repoA", "a.env", rules=rules))

    def test_outside_allowlist(self):
        self.assertIsNotNone(guard.check_tool("Read", {"file_path": os.path.join(self.tmp, "outside.txt")},
                                              self.code, self.rules))

    def test_write_new_ignored_file_blocked(self):
        self.assertIsNotNone(self.read("repoA", "new.env"))

    def test_grep_explicit_ignored_path_blocked(self):
        path = os.path.join(self.code, "repoA", "ignored-sub")
        self.assertIsNotNone(guard.check_tool("Grep", {"pattern": "x", "path": path}, self.code, self.rules))

    def test_grep_open_path_passes(self):
        path = os.path.join(self.code, "repoB")
        self.assertIsNone(guard.check_tool("Grep", {"pattern": "x", "path": path}, self.code, self.rules))

    def test_all_tools_allowed(self):
        for tool in ("WebFetch", "WebSearch", "Skill", "mcp__x__y", "Agent"):
            with self.subTest(tool):
                self.assertIsNone(guard.check_tool(tool, {"url": "https://example.com"}, self.code, self.rules))

    def test_shell_modes(self):
        self.assertIsNotNone(guard.check_tool("Bash", {"command": "ls"}, self.code, self.rules))
        allow = dict(self.rules, shell="allow")
        self.assertIsNone(guard.check_tool("Bash", {"command": "ls"}, self.code, allow))
        self.assertIsNone(guard.check_tool("PowerShell", {"command": "ls"}, self.code, allow))

    def test_git_failure_fails_closed(self):
        broken = subprocess.CompletedProcess(args=[], returncode=128, stdout=b"", stderr=b"fatal")
        with mock.patch.object(guard.subprocess, "run", return_value=broken):
            with self.assertRaises(RuntimeError):
                self.read("plain.txt")

    def test_main_blocks_on_git_failure(self):
        # End to end: a broken git must produce exit code 2, not a silent pass
        env = dict(os.environ, PATH=os.path.dirname(sys.executable), CC_GUARD_LOG=os.path.join(self.tmp, "g.log"))
        rules_path = os.path.join(self.tmp, "rules.json")
        import json
        with open(rules_path, "w", encoding="utf-8") as f:
            json.dump(dict(self.rules), f)
        env["CC_GUARD_RULES"] = rules_path
        payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": self.code,
                              "tool_input": {"file_path": os.path.join(self.code, "plain.txt")}})
        proc = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "guard.py")],
                              input=payload.encode("utf-8"), env=env, capture_output=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
