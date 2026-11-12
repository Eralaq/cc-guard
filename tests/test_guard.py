import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import guard  # noqa: E402

ROOT = os.path.realpath(os.path.dirname(__file__))
ALLOWED = os.path.join(ROOT, "allowed")
RULES = {
    "words": ["zzforbidden", "тестслово"],
    "regexes": [r"ACME-\d{4}"],
    "allow_paths": [ALLOWED],
    "allow_tools": ["TodoWrite", "Task"],
    "shell": "deny",
}


class PromptTests(unittest.TestCase):
    def test_clean_prompt_passes(self):
        self.assertIsNone(guard.check_prompt("hello world", RULES))

    def test_word_case_insensitive(self):
        self.assertIsNotNone(guard.check_prompt("look at ZZForbidden", RULES))

    def test_cyrillic_word(self):
        self.assertIsNotNone(guard.check_prompt("ТЕСТСЛОВО тут", RULES))

    def test_regex(self):
        self.assertIsNotNone(guard.check_prompt("deal acme-1234", RULES))

    def test_reason_hides_value(self):
        reason = guard.check_prompt("zzforbidden", RULES)
        self.assertNotIn("zzforbidden", reason)


class ToolTests(unittest.TestCase):
    def call(self, tool, **ti):
        return guard.check_tool(tool, ti, ROOT, RULES)

    def test_read_allowed(self):
        self.assertIsNone(self.call("Read", file_path=os.path.join(ALLOWED, "a.txt")))

    def test_read_outside(self):
        self.assertIsNotNone(self.call("Read", file_path=os.path.join(ROOT, "secret", "s.txt")))

    def test_read_traversal(self):
        self.assertIsNotNone(self.call("Read", file_path=os.path.join(ALLOWED, "..", "secret", "s.txt")))

    def test_read_prefix_sibling(self):
        # "allowed-evil" must not match the "allowed" root
        self.assertIsNotNone(self.call("Read", file_path=ALLOWED + "-evil" + os.sep + "a.txt"))

    def test_grep_default_path_blocked(self):
        self.assertIsNotNone(self.call("Grep", pattern="x"))

    def test_grep_allowed_path(self):
        self.assertIsNone(self.call("Grep", pattern="x", path=ALLOWED))

    def test_glob_escape(self):
        self.assertIsNotNone(self.call("Glob", pattern="../secret/*", path=ALLOWED))

    def test_shell_denied(self):
        self.assertIsNotNone(self.call("Bash", command="grep -r banana *"))
        self.assertIsNotNone(self.call("PowerShell", command="Get-Content x"))

    def test_shell_allow_switch(self):
        rules = dict(RULES, shell="allow")
        self.assertIsNone(guard.check_tool("Bash", {"command": "ls"}, ROOT, rules))

    def test_unknown_tool_denied(self):
        self.assertIsNotNone(self.call("WebFetch", url="https://example.com"))

    def test_allowed_tool(self):
        self.assertIsNone(self.call("TodoWrite", todos=[]))

    def test_forbidden_text_in_tool_input(self):
        self.assertIsNotNone(self.call("Read", file_path=os.path.join(ALLOWED, "zzforbidden.txt")))


if __name__ == "__main__":
    unittest.main()
