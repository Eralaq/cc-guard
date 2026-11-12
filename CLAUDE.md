# cc-guard

Claude Code hook (`guard.py`) that blocks forbidden words/regexes in prompts and enforces a path allowlist for tools.

- The real rules file (`~/.cc-guard/rules.json`) holds company names and forbidden words. NEVER read it, print it or commit it. Use `rules.example.json` and test placeholders only.
- Block reasons must never repeat a matched value.
- Fail-closed: errors block the request.
- Shell tools are denied by default; do not add shell parsing as a protection (proven bypassable).
- Tests: `python -m unittest discover -s tests`. All code comments in English.
