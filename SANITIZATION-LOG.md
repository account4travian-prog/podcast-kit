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

## NEEDS-REVIEW (owner should eyeball)

1. The legacy standalone render script's default host speaker name
   was changed to "Host" (a string default). Behaviour is otherwise
   untouched; pick a different placeholder if you prefer.
2. The `doctor` command retains a generic entry-ID parsing
   convention (letter-dash-numbered entries in a producer-notes
   file) inherited from the original governance format. The shipped
   template uses plain numbered-ruling headings, so that branch is
   inert. Decide whether to keep the convention for your own notes.
3. `SOURCES.json` now pins the shipped templates. If you restore
   real governing documents beside the kit, re-pin with
   `moe.py doctor --docs <dir> --accept`.
4. The example personas and occasions are invented fixtures written
   for this copy (three guests, two fictional studies). Confirm you
   are happy publishing even fictional example content under your
   account.
5. The panel example intentionally leaves two voices unset (null) to
   demonstrate the casting gate, mirroring the original fixture.
6. The internal live-test report was dropped rather than rewritten;
   the README testing summary is new prose - check it says nothing
   you disagree with.
7. No standalone unit-test code existed in the original `tests/`
   folder (only live-run outputs), so no test code ships. If you
   want public tests, they would have to be written fresh.
8. The kit still names its third-party speech service and model
   family by their public product names - a real dependency of the
   code, kept deliberately.
9. Internal production dates were scrubbed from the README and
   runbook; the copy is undated and unversioned. Add a version line
   if you want one before publishing.

## Addendum (2026-11-09, owner ruling)
- License set by owner's direction ("whatever license makes sense for a tool like this"): MIT License, copyright holder "account4travian" (the publishing account — the only place that identifier appears in the tree, by owner's ruling). README license line updated to match.

## Addendum 2 (2026-11-09, pre-posting pass)
- Removed `moekit/__pycache__/` (bytecode created by a local doctor run; .pyc files embed local source paths — caught by a repeat denylist sweep, zero hits in any text file before and after).
- Added `.gitignore` (Python bytecode and venvs) and `CONTENTS.md` (a map of the tree) for posting. File count is now 40.
