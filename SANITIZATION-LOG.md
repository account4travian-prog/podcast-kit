# Sanitization Log

How this public copy was produced from the internal production kit.
Removed strings are described by class only; nothing personal is
quoted in this file.

## Method
Scripted copy of the code, prompts and docs, followed by automated
class replacements, hand-rewritten examples/templates/README/runbook,
and a denylist verification pass (below). Line endings of copied
files were preserved from the originals, so code diffs stay limited
to the sanitized lines (the largest code file differs in 24 lines,
all strings and comments). The original kit was not
modified. Nothing was pushed or published; this folder is a build
artifact for the owner's review.

## Classes of change

1. **Producer's personal name** - replaced everywhere with "the
   producer" / "the producer's". Files: `moe.py`, `moekit/record.py`,
   `moekit/room.py`, `SKILL.md`, `README.md`.
2. **Show title** - the original show's three-word title replaced with
   "Example Show" in example material and generic wording ("the show")
   elsewhere. Files: `prompts/*.md`, `moe.py` docstring,
   `examples/agent-system-prompt.txt`.
3. **Cast names** - the three guest characters' first and last names
   replaced with clearly fictional example personas (new invented
   personas with placeholder surnames). Files: rewritten `examples/`,
   the floor-offer example in `prompts/host-turn.md`, example strings
   in `moekit/lint.py`, the docstring in `moekit/remix_turns.py`.
4. **Host character name** - replaced with the generic role name
   "Host" in prose, docstrings, string defaults and examples.
   Files: `moe.py`, `moekit/room.py`, `moekit/render_drama3.py`,
   `examples/`.
5. **Internal ruling codes** - the production's ruling identifiers
   (a capital letter, a dash, and digits) replaced with generic
   phrasing ("producer ruling"). Files: comments and message strings
   in `moekit/*.py`, `prompts/cast-voice.md`, `SKILL.md`, `README.md`.
   One automated replacement initially damaged an unrelated encoding
   string containing a similar pattern in a pricing note; caught in
   verification and restored (`moekit/render_drama3.py`).
6. **Voice model identifiers** - real text-to-speech voice model IDs
   (32-character hex strings) replaced with the placeholder
   "voice-model-id"; unset voices stay null. Files: `examples/`
   configs, the docstring example in `moekit/render_drama3.py`.
7. **Absolute and private paths** - home-directory absolute paths and
   internal folder layouts replaced with relative paths inside this
   folder or <placeholder>-style paths. Files: `examples/` configs,
   `SKILL.md` setup section, one code comment in `moekit/room.py`.
   The internal setup section also carried a private
   credential-retrieval command for the original machine; replaced
   with a plain environment-variable instruction.
8. **Private governing documents** - the kit originally pointed at
   production documents that live outside the kit (an avoid-list, a
   presentation guide, a pipeline description, a producer-rulings
   file). Generic template versions, rewritten without the show's
   canon, characters or history, ship in `templates/`, and the
   example configs point at them.
9. **People, vendors and internal doc names in prose** - a named
   independent auditor became "an independent auditor"; named AI
   models/tools in the testing and runbook sections became "a small
   model" / "an agent CLI"; internal episode-log, format and
   integration documents became generic descriptions. Files:
   `README.md`, `SKILL.md`.
10. **Contact and network identifiers** - none were present in the
    kept files (verified: no email addresses, no account identifiers,
    no IP addresses, no private web domains). The classes were in the
    denylist scan regardless.

## Dropped (not copied)

- `work-orders/` (two work orders and one room config) - production
  instructions naming the real show, its folders and its people.
- `tests/` live-run folders (six runs: transcripts, scratch files,
  run records, one audio file, measurement JSON) - real show content
  from internal testing. `tests/` ships as a README explaining this.
- The internal live-test report at the kit root - a test write-up
  naming the real cast and occasion. The README's testing section is
  a fresh generic summary instead.
- The original `SOURCES.json` - it pinned hashes of the private
  governing documents. Regenerated against the shipped templates.
- The original example configs and occasions - replaced with the
  fictional fixtures in `examples/`.
- Python bytecode caches - build artifacts.

## Verification

Denylist scan over the finished copy, this log included. Term
classes and hit counts: producer's personal name 0; private web
domain 0; home-directory absolute path prefix 0; guest first names
(three) 0 each; guest surnames (three) 0 each; original show title 0;
mail-provider name 0; account identifiers (two) 0 each; ruling-code
prefix (letter-dash-zero) 0; standalone 32-character hex voice IDs 0.
Also scanned clean: email-address pattern, IP-address pattern,
absolute home paths.

Self-tests run against this copy (no API key): the code compiles;
`moe.py doctor --docs templates` reports OK; `moe.py help` runs; the
example one-on-one room passes config validation and creation in
place (`moe.py new`), with persona and template paths resolving
relative to the room file.

## Addendum (2026-11-09, owner ruling)
- License set by owner's direction ("whatever license makes sense for a tool like this"): MIT License, copyright holder "account4travian" (the publishing account — the only place that identifier appears in the tree, by owner's ruling). README license line updated to match.

## Addendum 2 (2026-11-09, pre-posting pass)
- Removed `moekit/__pycache__/` (bytecode created by a local doctor run; .pyc files embed local source paths — caught by a repeat denylist sweep, zero hits in any text file before and after).
- Added `.gitignore` (Python bytecode and venvs) and `CONTENTS.md` (a map of the tree) for posting. File count is now 40.

## Addendum 3 (2026-11-09, rename)
- Owner's direction: the repository and display name changed from "moe-kit" to "podcast-kit" — MoE was the internal project name, and an unrelated AI-themed podcast already uses the show's name. Only display/title strings changed; the program's filenames (`moe.py`, the `moekit/` package) are unchanged.

## Addendum 4 (2026-11-09, diagram)
- Added `podcast-kit-flow.png` (a workflow diagram of the kit) at the owner's request and embedded it in the README. File count is now 41.

## Addendum 5 (2026-11-09, cast-design)
- Added `cast-design` (owner-directed): voice-candidate generation inside the kit - design from the accepted brief, one trained model per candidate, quick/slow gate renders - closing the gap the README's testing note recorded. Marked ALPHA in the help text, the runbook, the README and the command's own banner; live-tested end to end once before posting (design -> train -> render -> cast-check).
