---
name: moe-room-production
description: Produce conversation audio with podcast-kit — prepare a room, run it turn by turn with fresh agents (the kit does all plumbing, checks and counting), render with Fish Drama 3 in scene mode, mix, report and deliver; also the performance-only re-tag pass, voice casting gates, and assembling rooms into an episode part.
---

# Room production with moe-kit

This is the procedure for making the show's conversations. It covers the
room engine (PIPELINE.md), the Drama 3 render, and the
producer rulings rules. The kit (`moe.py`) does every mechanical step. **Your job
is the parts that need judgment: preparing the room, spawning agents, and
reporting honestly.**

Governing law still lives in the documents: PRODUCER-NOTES.md rulings,
PIPELINE.md, AVOID-LIST.md, PRESENTATION.md (generic templates ship in `templates/`).
The kit's prompts restate those rules. `moe.py doctor` tells you when a
document has changed since the prompts were written.

## The ten rules (read these every run)

1. **Never write a line for anyone but yourself as host, and even then only
   through the kit.** Guests' words come only from their own fresh agents.
2. **One fresh agent per action.** Never reuse an agent, never give one
   agent two prompts, never add your own notes to a prompt.
3. **Never edit a prompt file or a reply.** Paste the reply exactly as the
   agent wrote it. If it's bad, the kit asks for a repair; that's its job.
4. **Never type a number into a record.** Word counts, objections, loudness,
   pace and hashes all come from the kit (`moe.py record`). If you mention a
   number to the producer, copy it from RUN-RECORD.md.
5. **If the kit prints `STOP:`, stop.** Report the exact message to the producer.
   Don't work around it.
6. **Run `moe.py doctor` before every room.** If it says NOT OK because a
   governing document changed, read the change. If it alters a rule a
   speaker, watcher or auditor follows, tell the producer before running.
7. **Holds and ship gates still apply.** Check your production's episode
   log for a HOLD before producing anything, and whatever sign-offs your
   production requires (for example a disclosure sign-off and a host-profile
   review) before producing audio for an episode. (Sample runs the producer
   orders directly are not episode production.)
8. **Agents get no tools and no files.** Everything they need is in the
   prompt. Run them in an empty folder.
9. **Report what happened, not what should have happened.** A failed step
   reported plainly is a delivery; a silent gap is the failure.
10. **The producer's ear decides.** Prosody spreads, audit counts and
    watcher statistics are evidence for the producer, not verdicts.

## 0. Setup (once per machine)

```
python moe.py doctor
```
It needs ffmpeg/ffprobe on PATH and Python with numpy, scipy and requests.
Fish rendering needs `FISH_API_KEY` in the environment of the render
command only, never written into a file. Set it for that one command, e.g.
`FISH_API_KEY=<your key> python moe.py render ROOM_DIR`.

## 1. Prepare a room

A room is one conversation segment: one host, one to three guests, about
750–1,200 spoken words (5–8 minutes). Make a folder for it and write two
files there. Copy `examples/one-on-one/` as a starting point.

**`occasion.md`: the shared brief.** These are the only outside facts every
agent gets.
- Facts only, each with its source and date. Include the caveats the source
  itself gives.
- No opinions, no "the interesting question is…", no conclusions.
- Numbers as words where they'll be spoken.

**`room.json`: the configuration.**

| field | what to put |
|---|---|
| `title` | short name of the room |
| `cast[]` | one host + 1–3 guests: `name` (the short name used on air), `full_name`, `role` (`host`/`guest`), `voice` (Fish model id, from CASTING / the dossier), `files` (the dossier; for the host, the voice section of the host profile plus a host life-notes file) |
| `occasion_file` | `occasion.md` |
| `run_of_show.topics` | 3–5 topics as plain phrases: where the talk can go, NOT questions to read out |
| `run_of_show.friction` | ONE real point where the host disagrees, or a counter-case from the source (producer ruling: friction is booked) |
| `run_of_show.word_budget` | 750 for about 5 minutes |
| `run_of_show.hard_stop` | about 1.2 × budget |
| `run_of_show.opening_note` | what the host's first turn must contain (e.g. the disclosure, for a standalone sample) |
| `prior_rooms` | (episodes) folders of rooms already recorded for this episode, in order |
| `standards` | paths to AVOID-LIST.md and PRESENTATION.md (generic templates in `templates/`) |

Paths are relative to `room.json`. Then:
```
python moe.py new ROOM_DIR --config ROOM_DIR/room.json
```
If it prints `STOP:`, the message names the missing field or file. Fix it
and run the command again.

## 2. Run the room

**Use your parallel agents** (you can run 5 at once). Put
`"parallel": {"candidates": 2}` in room.json (or run
`moe.py parallel ROOM --candidates 2`). Then:
- **each turn is drafted by 2 fresh agents at once,** and the kit uses the
  first draft, in fixed order, that passes the code checks. A failed draft
  used to cost a whole repair round trip. Choosing is mechanical, never
  taste, and the unused draft enters no one's context.
- **re-checks run as side jobs.** A restated turn commits whatever the
  re-check says (no second veto, producer ruling), so the room moves on while the
  re-check finishes. Its ruling still goes on the record.
- **re-tags all run at once** (one job per committed turn).

`next` lists every job that can run now. Start them all, up to 5. Run
`submit` when they're saved; it processes whatever is complete.

**Manual loop** (spawning subagents yourself):
```
python moe.py next ROOM_DIR
```
It prints exactly one action:
1. Start a fresh agent.
2. Give it the whole prompt file named.
3. Save its whole reply to the reply file named.
4. Run `python moe.py submit ROOM_DIR`.

Repeat `submit` → follow the printed instructions until it says
`ROOM CLOSED`.

**Auto loop** (when an agent command is available):
```
python moe.py run ROOM_DIR --jobs 5 --cwd EMPTY_DIR --agent "<command that reads a prompt on stdin and prints the reply>"
```
Example shape, with any agent CLI that reads a prompt on stdin and prints
the reply:
`--agent '<agent-cli> --system-prompt-file examples/agent-system-prompt.txt'`.
The loop resumes where it stopped if interrupted; just run it again.

**Whatever spawns the agents, give each one the one-line system prompt in
`examples/agent-system-prompt.txt`** (a writing agent in an openly AI-made
podcast's production pipeline). In testing, an agent running under a
general coding-assistant system prompt declined to play a guest. Under the
production prompt, the same action went through.

What the kit does for you, so you don't:
- Picks the next speaker:
  - one-on-one: alternation;
  - panels: the person addressed by name, otherwise the guest with the
    fewest words;
  - after three guest turns in a row, the host.
- Builds each prompt with ONLY what that role may see. A guest gets their
  own dossier, the brief and the committed transcript; never proposals,
  never other dossiers.
- Checks every reply in code: format, digits, tag syntax and budgets,
  name-only floor offers, the 50-word guest limit, structural repeats,
  missing contractions. It asks the same role for one format repair, then
  moves on.
- **Enforces the turn budgets (producer rulings) with length cues.** Prompts
  alone weren't enough: in the live test, a weaker model ignored "most turns
  are ten to thirty words" and wrote 29–59 words every turn. So the kit sets
  a length for the turn, and the linter holds the speaker to it:
  - when under a third of turns so far are short, the next turn is cued
    "ten words or fewer";
  - a guest whose last turn ran over thirty is cued "thirty or fewer";
  - host turns are capped at forty (opening and close excepted).

  These are lengths, never wording.
- **Cues the booked friction.** If the host hasn't pushed back on the booked
  point by half the word budget, their next turn is told it's due now.
- **Cues topic moves.** When the furthest topic reached has used about 1.3×
  its share of the budget, the host is told to move to the next topic
  (by their own TOPIC labels). This stops the stalls seen in testing, like
  seven turns of "we'll check the page."
- **Rejects copied delivery directions.** Weaker models copy examples:
  "[quick, low, almost to herself]" appeared in most turns of one test,
  from the host too. A direction copied from the card, or reused from the
  last two turns, is a format error.
- Runs the watcher, then checks the watcher. An objection must quote the
  proposal exactly, and its "without" text may only delete words. Then it
  runs one restatement and one re-check, and commits (no second veto).
- Tells the host when to close, then runs the independent audit.
- Writes TRANSCRIPT.md (performance script), TRANSCRIPT-DELIVERED.md
  (tags stripped), scratch/<name>.md and state.json (every prompt and reply
  hashed).

`python moe.py status ROOM_DIR` shows words used, short-turn share, guest
median and whether the booked friction happened, at any time.

## 3. Render, mix, report, record

```
python moe.py probe ROOM_DIR                     # ~300 bytes: key, drama-3, every cast voice, a 2-voice scene
python moe.py record ROOM_DIR                    # first record, before audio
python moe.py render ROOM_DIR --takes 2          # scene mode, Drama 3, temperature 0.8
python moe.py split ROOM_DIR                     # scene takes -> turns/T##.wav
python moe.py mix ROOM_DIR                       # per-speaker gain, joins, -16 LUFS linear master
python moe.py report ROOM_DIR                    # prosody spreads -> prosody.json
python moe.py record ROOM_DIR                    # final RUN-RECORD.md
```
- Run `probe` before the first render of the day. If it prints PROBE
  FAILED, fix that line first.
- Always run `render ... --dry-run` first. It prints the scene plan and the
  cost.
- **Run `split` with FISH_API_KEY set.** It then transcribes each scene take
  with Fish speech-to-text and cuts at the real turn changes. This is
  verified on a real render: 95–98% of words matched, and it fixed a cut the
  acoustic method got wrong. Without the key, it falls back to the acoustic
  split and the record says so.
- **Takes:** take 1 is used unless you change `"chosen"` for a unit in
  `render-scene/manifest.json`. Don't pick takes by your own judgment of
  audio you can't hear. Use take 1, or send the producer the takes if he asked to
  choose.
- **Temperature sweep** (only when the producer asks for one, or for a new voice):
  render the same room at `--temperature 0.7`, `0.8` and `0.9` into copies
  of the room folder. Name the folders by temperature. Send the producer one
  scene from each.
- **If `split` warns of a loose fit,** say which unit in the delivery note.
  Or re-render that room with `--mode turn`, then `moe.py split ROOM --mode
  turn`.
- **The mix never uses per-turn normalization or single-pass `loudnorm`.**
  Both flatten delivery (producer ruling). Don't add them.

**Deliver** to the producer:
- `mix/room-mix.mp3`
- TRANSCRIPT-DELIVERED.md
- RUN-RECORD.md
- one short message, with the numbers copied from RUN-RECORD.md:
  - runtime and loudness;
  - turns and words;
  - short-turn share;
  - whether the friction happened;
  - audit flags;
  - the prosody spreads;
  - anything the record marked as a WARNING.

Then record the producer's verdict in PRODUCER-NOTES.md, in their words, as a new entry.

## 4. Performance-only pass (same words, new performance)

Use this to test whether the performance changes the result with the
words held fixed.
```
python moe.py import OLD_ROOM --config room.json --transcript <path to a committed TRANSCRIPT.md>
python moe.py retag OLD_ROOM           # then the same next/submit loop, or `run`
python moe.py render OLD_ROOM --script retagged --takes 2
python moe.py split OLD_ROOM && python moe.py mix OLD_ROOM && python moe.py report OLD_ROOM && python moe.py record OLD_ROOM
```
In a re-tag, each speaker's own fresh agent re-marks only their own turns.
The kit rejects any reply whose words differ from the committed words. After
one failed repair, it keeps the original tags.

## 5. Casting a voice (producer ruling step 3; any new guest)

```
python moe.py cast-brief CAST_DIR --name Alex --full-name "Alex Example" --dossier examples/personas/alex-example.md
```
1. Give the prompt to a fresh agent and save its reply as
   `CAST_DIR/cast.reply.md`.
2. Run `python moe.py cast-submit CAST_DIR`. It rejects projection words
   and "never slow" instructions.
3. Generate **4–5** Fish voice-design candidates from INSTRUCTION, using
   QUICK_LINE + WEIGHTY_LINE as the reference text. Save each as
   `cand-N.wav`.
4. For each candidate, render:
   - QUICK_LINE as `[quick, light, offhand] …` → `cand-N-quick.wav`;
   - WEIGHTY_LINE as `[slow, low, every word placed] …` → `cand-N-slow.wav`.
5. Run:
   ```
   python moe.py cast-check CAST_DIR --compare <a file of another cast voice>
   ```
   **A near miss on directability (rate ratio 1.15–1.25) is not a fail yet.**
   One render is noisy: in testing, the current Host and Alex voices
   both measured 1.22–1.23 while moving pitch 6–7 semitones. Render the
   quick and slow lines again (`cand-N-quick.wav` / `cand-N-slow.wav`
   overwritten) and re-check. Report both readings to the producer.
   It tests bandwidth (rolloff ≥ 2.5 kHz; presence within 6 dB of the
   comparison) and directability (rate changes ≥ 25% between the quick and
   slow lines, and level or pitch also moves). Pace is reported, not gated.
6. Only survivors go to the producer's ear. If nothing survives, generate more
   candidates. Never cast a voice that failed a gate.
7. Record the instruction, the pick, the model id and the gate numbers in
   the guest's dossier and CASTING.md.

## 6. Building an episode part from rooms

An episode may be built from several parts, and each part is a sequence
of segments. Your production's format document governs the order. Each segment is one
of:

| segment | how to make it |
|---|---|
| Disclosure tag (fixed words) | `import` a one-turn transcript containing exactly the disclosure sentence (`**T1 · Host:** …`), render `--mode turn` |
| Cold open (a guest's strongest line, verbatim) | AFTER the rooms exist: copy the exact sentence from a committed room's TRANSCRIPT.md into a one-turn transcript in that guest's name; `import` and render `--mode turn`. Note in the record which room and turn it came from |
| Promise / framing / recap & close (host alone) | a **solo room**: cast = host only; `run_of_show` = `{"solo_brief": "<what the segment must do>", "word_budget": N}`; `prior_rooms` = every room before it. Same next/submit loop: one turn, watcher-audited |
| Deep interview / panel module | 2–5 rooms in a row, each with `prior_rooms` listing the earlier rooms of the episode, each with its own topics and friction |

Then write `PART.json`:
```json
{"file": "mixture-of-experts-ep04-part1.mp3", "gap_s": 0.8, "sting": null,
 "band_minutes": [15, 25],
 "segments": [{"room": "rooms/00-disclosure", "title": "Disclosure"},
              {"room": "rooms/01-cold-open", "title": "Cold open"},
              {"room": "rooms/02-promise", "title": "The question"},
              {"room": "rooms/03-interview-a", "title": "First interview"}]}
```
```
python moe.py episode PART.json
```
It writes the part mp3 (one linear master over everything), chapters.txt
and episode.json (minutes, in-band check, loudness, hash). If the part is
outside its band, the kit says so. Report it; don't pad or cut rooms to hide
it.

**Before delivery of an episode part**, the existing gates still run, on
the committed transcripts:
- an independent pre-release audit;
- the claims check against the occasion briefs;
- your dropout scan / listening rubric, where your integration notes specify them.

The kit doesn't replace them.

## 7. When something goes wrong

| You see | Do this |
|---|---|
| `STOP: ... file not found` | fix the path in room.json; `moe.py new` into a NEW folder |
| `reply file not found` | you saved the reply somewhere else; save it to the exact path printed |
| `the prompt file was edited` | you or a tool changed it; run `moe.py reissue ROOM` and give the NEW prompt to a NEW agent |
| `two unusable replies in a row` | the agent probably didn't get the whole prompt (truncation?). Check, then `moe.py reissue ROOM` |
| `repair-requested` | normal. Run `next` and give the repair prompt to a fresh agent |
| `STOP: the agent ... DECLINED the role` | an agent refused to play the character. Don't pressure it and don't edit the prompt to get around it. Tell the producer what it said. (In testing this happened once, when the prompt didn't say the show discloses its guests as AI characters. The prompts now say so.) |
| watcher objections on most turns | normal for early rooms. Don't intervene; the record will show it |
| `doctor` says a document CHANGED | read the diff; if a rule changed, tell the producer before running a room |
| render HTTP errors | retry later; if it persists, report the exact error line. Never switch models silently (no `model` header = s2.1-pro fallback) |
| split "loose fit" warning | listen-check is the producer's; re-render that room in turn mode if he asks |
| the part runs outside its band | report minutes and band; ask the producer whether to add or drop a room |

## What the kit does not do

- It doesn't decide topics, casts, friction points or episode structure.
  That's preparation.
- It doesn't listen. It measures. The producer's ear decides.
- It doesn't replace independent audits, holds, ship gates, the claims log, or delivery
  timing.
- It doesn't publish anything. Delivery is private, to the producer.
