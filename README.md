# Patch Studio

A deterministic desktop tool for reviewing, validating, and applying unified diff patches to a local project directory.

Patch Studio exists because command-line patch tools will happily half-apply a diff and leave you to work out what happened. This one won't: nothing touches disk until a dry-run has succeeded and you have explicitly confirmed it.

**Normalize → Parse → Preflight → Preview → Apply.** Every stage is visible, every write is backed up, every path is bound to the workspace root.

---

## The workflow

1. **Select a workspace root.** Every patch path resolves relative to it. Anything that escapes the root is blocked.
2. **Load a patch** from a file or paste it in. Git, index-style, and classic unified diffs are detected automatically.
3. **Preflight.** Resolves each referenced file and reports what's ready, missing, blocked, or binary — before anything runs.
4. **Preview.** A full in-memory simulation. Conflicts surface here, not on your filesystem.
5. **Apply.** Enabled only after a clean preview. Backs up, then writes atomically.

Apply stays disabled until preview succeeds. Conflicted output stays blocked unless you explicitly unblock it in Advanced.

---

## Capabilities

**Diff dialects:** `diff --git`, index-format, classic unified. Dialect detection is automatic.

**Operations:** create, modify, delete, rename (gated), mode change (gated).

**Preview engine:** in-memory simulation, conflict detection ahead of apply, explicit blocking on unsafe conditions, no silent partial application.

**Backups:** every modified file is copied to `<root>/.patchstudio_backups/<timestamp>/` before it is touched. Writes are atomic.

**Advanced controls** (hidden by default — these deliberately relax the gates):

| Control | Effect |
|---|---|
| Strict filename match | Refuse near-miss path resolution |
| Best-effort fuzzy apply | Search a window around the expected line |
| Ignore whitespace differences | Match context ignoring leading/trailing space |
| Conflict marker mode | Emit 3-way style markers instead of failing |
| Allow rename/delete/mode changes | Ungate metadata operations |
| Partial apply per-file override | Let a file apply with some hunks failed |
| Preserve original line endings | Keep CRLF/LF as found (on by default) |
| Allow writing conflicted output | Write files containing conflict markers |
| Skip unsupported binary files | Continue past binary hunks (on by default) |

---

## The interface

Dark instrument-panel aesthetic: obsidian ground, teal for focus and active state, phosphor green for go, amber for gated, red for blocked. Monospace throughout, zero-radius controls, hairline rules.

State is carried by colour, not decoration. Each session card has an accent rail — grey for nothing loaded, teal for loaded, green for armed, amber for gated, red for blocked — so the state of the apply gate is legible at a glance.

Every colour in the app comes from one token table in `patchstudio/ui/theme.py`. Nothing else in the package hard-codes an RGB value, so retheming is a single-file edit.

---

## Install and run

Requires **Python 3.10+** and **PyQt6**.

```bash
pip install -r requirements.txt
```

Run from the `src` directory:

```bash
python -m patchstudio.app
```

Or via the entry script:

```bash
python run_patchstudio.py
```

---

## Developer utilities

**Built-in engine checks**, no GUI required:

```bash
python -m patchstudio.app --selftest
```

**Test suite:**

```bash
cd src
pytest tests
```

Headless environments need `QT_QPA_PLATFORM=offscreen`.

**Package layout:**

```
patchstudio/
  core/     normalizer · parser · applier · diffgen · models · selftests
  ui/       theme · main_window · models · delegates · dialogs
```

Core carries no Qt dependency and can be driven headlessly.

---

## Design principles

- Determinism over automation
- Operator visibility over silent correction
- Explicit gating over implicit behaviour
- Safety over speed
- Reversibility over convenience

---

## Non-goals

Patch Studio is not a Git client, a history manager, a 3-way merge tool, or a CI/CD replacement. It operates strictly on the current filesystem state.

---

## Roadmap

- CLI-only execution mode
- Patch signing and integrity verification
- Richer conflict visualisation
- Structured log export

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

Copyright © 2026 Leon Priest ([7h3v01d](https://github.com/7h3v01d)).
