import json
import os
import re
import unittest

PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "regex-examples.json")


class RegexExampleTests(unittest.TestCase):
    def test_examples_match_and_do_not_match(self):
        with open(PATH, encoding="utf-8") as f:
            examples = json.load(f)
        self.assertGreater(len(examples), 5)
        for ex in examples:
            with self.subTest(ex["name"]):
                # guard.py compiles rules with re.IGNORECASE, do the same here
                rx = re.compile(ex["regex"], re.IGNORECASE)
                self.assertIsNotNone(rx.search(ex["match"]))
                self.assertIsNone(rx.search(ex["no_match"]))


if __name__ == "__main__":
    unittest.main()
