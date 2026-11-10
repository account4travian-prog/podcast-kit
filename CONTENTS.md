# Contents — what is in this repository

Everything needed to run the kit is in this folder. Start with README.md.

## Start here
- `README.md` — what the kit is, how a run works, setup
- `SKILL.md` — the operator runbook: the exact loop to run a room
- `LICENSE` — MIT
- `CONTENTS.md` — this map
- `SANITIZATION-LOG.md` — what was removed or genericized for public release

## The program
- `moe.py` — the single command-line entry point (`next`, `submit`, `render`, `mix`, `episode`, `cast-*`, `doctor`, ...)
- `moekit/room.py` — the room state machine: who speaks, prompt assembly, access control per role
- `moekit/lint.py` — code-level checks on every proposed turn (format, length, tags, structure)
- `moekit/casting.py` — voice design and the casting gates (bandwidth, directability)
- `moekit/audio.py` — rendering, splitting, mixing, loudness
- `moekit/record.py` — the run record: every number a report may quote comes from here
- `moekit/render_drama3.py`, `moekit/remix_turns.py`, `moekit/prosody_report.py` — audio tools
- `SOURCES.json` — hashes of the shipped template docs; `moe.py doctor` flags drift

## Role prompts (what each agent sees)
- `prompts/host-turn.md`, `prompts/guest-turn.md` — speaking roles
- `prompts/watcher.md` — the per-turn checker
- `prompts/auditor.md` — the whole-room audit after the room closes
- `prompts/cast-voice.md` — writing a voice-design brief
- `prompts/retag.md` — performance-only re-tagging of fixed words
- `prompts/_speech-card.md`, `prompts/_performance-card.md` — the shared standards cards

## Standards templates (replace with your own show's rules)
- `templates/AVOID-LIST.md` — the deletion test and confirmed AI-ism shapes
- `templates/PRESENTATION.md` — delivery directions, span tags, punctuation as prosody
- `templates/PIPELINE.md` — the room protocol end to end
- `templates/PRODUCER-NOTES.md` — where producer rulings are recorded

## Examples (fictional fixtures)
- `examples/one-on-one/` — a two-person room config + occasion
- `examples/panel/` — a four-person room config + occasion
- `examples/personas/` — the four invented example personas those rooms use
- `examples/episode-part.json` — part assembly: segment order and target length band
- `examples/agent-system-prompt.txt` — the wrapper brief for spawned agents

## Tests
- `tests/README.md` — what was tested and how; no live-run outputs ship
