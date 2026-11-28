# Aptus: instructions for agents

- Every build gets version notes. Before pushing changes that ship, add an entry at the top of `release_notes.json` (or extend the top entry if it hasn't been released) describing the features added, in plain language for Tim. The newest entry sets the build's version number, and the notes show at the bottom of Settings.
- Read `DESIGN.md` and the family design system before changing the interface.
- Run `python -m pytest -q` before pushing.
