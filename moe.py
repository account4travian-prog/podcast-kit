#!/usr/bin/env python3
"""moe-kit: run a Example Show conversation room, render it, mix it, record it.

The kit does the plumbing; agents only write. Start with:  python moe.py help
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

KIT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, KIT)

from moekit.room import Room, RoomError  # noqa: E402
from moekit import record as rec  # noqa: E402

HELP = """
THE WHOLE LOOP (one room = one conversation segment, ~5-8 minutes)

  1. python moe.py new ROOM_DIR --config room.json     create the room
  2. python moe.py next ROOM_DIR                       says exactly what to do
       -> start a FRESH agent, give it the prompt file, save its reply where told
  3. python moe.py submit ROOM_DIR                     the kit checks it and moves on
     repeat 2-3 until `next` says ROOM CLOSED            (or: moe.py run ROOM_DIR --agent "<cmd>")
  4. python moe.py record ROOM_DIR                     writes RUN-RECORD.md
  5. python moe.py render ROOM_DIR [--takes 2]         Drama 3, scene mode (needs FISH_API_KEY)
  6. python moe.py split ROOM_DIR                      scene takes -> turns/T##.wav
  7. python moe.py mix ROOM_DIR                        per-speaker levels, joins, -16 LUFS master
  8. python moe.py report ROOM_DIR                     prosody spreads
  9. python moe.py record ROOM_DIR                     final record; deliver mix/room-mix.mp3 + record

Other commands:
  status ROOM          where the room is, budgets so far
  import ROOM --config room.json --transcript TRANSCRIPT.md   bring an existing committed room in (closed)
  reissue ROOM         re-issue outstanding prompts (agent crashed / reply lost)
  parallel ROOM --candidates N   N fresh agents draft each turn at once; the first that passes the checks is used
  retag ROOM           performance-only pass: each speaker re-marks their own turns, words fixed
  cast-brief DIR --name N --full-name "F N" --dossier FILE    guest authors their voice instruction
  cast-design DIR      ALPHA (in development): generate voice candidates inside the kit (needs FISH_API_KEY)
  cast-check DIR       measure voice candidates (bandwidth + directability gates)
  probe ROOM|room.json  cheap Fish preflight: key, drama-3, every voice, one two-speaker scene
  episode EP.json      join mixed rooms/segments into an episode, chapters, master
  doctor               check ffmpeg, python libs, docs, API key presence
"""


def cmd_new(a):
    r = Room.create(a.room, a.config)
    print(f"Room created at {r.path}\n")
    print(r.instructions())


def cmd_import(a):
    r = Room.import_transcript(a.room, a.config, a.transcript)
    print(f"Imported {len(r.s['turns'])} committed turns into {r.path} (phase: closed).")
    print("Next, for a performance-only pass: python moe.py retag ROOM   then render with --script retagged")


def cmd_next(a):
    print(Room(a.room).instructions())


def cmd_status(a):
    print(Room(a.room).status())


def cmd_submit(a):
    r = Room(a.room)
    res = r.submit()
    print(f"RESULT: {json.dumps(res)}\n")
    print(r.status() if a.verbose else r.instructions())


def cmd_reissue(a):
    r = Room(a.room)
    r.reissue()
    print(r.instructions())


def cmd_run(a):
    """Auto mode: runs every outstanding job through an external agent command (stdin -> stdout),
    up to --jobs at the same time, then submits; repeats until nothing is outstanding."""
    from concurrent.futures import ThreadPoolExecutor
    r = Room(a.room)
    rounds, failures = 0, 0
    cwd = a.cwd or None
    if cwd:
        os.makedirs(cwd, exist_ok=True)

    def call(prompt):
        try:
            return subprocess.run(a.agent, shell=True, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", timeout=a.timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            return None

    while rounds < a.max_steps:
        jobs = [(act, p) for act, missing in r.outstanding() for p in missing]
        if not jobs:
            break
        print(f"round {rounds + 1}: {len(jobs)} job(s): "
              + ", ".join(f"#{act['seq']} {act['kind']} {act['speaker'] or ''} T{act['turn'] or ''}" for act, _ in jobs),
              flush=True)
        prompts = [open(os.path.join(r.path, act["prompt"]), encoding="utf-8").read() for act, _ in jobs]
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            outs = list(ex.map(call, prompts))
        bad = 0
        for (act, p), out in zip(jobs, outs):
            if out is None or out.returncode != 0 or not out.stdout.strip():
                bad += 1
                print(f"   agent failed on #{act['seq']}: {'timeout' if out is None else out.stderr[-300:]}")
                continue
            with open(os.path.join(r.path, p), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out.stdout)
        failures = failures + 1 if bad == len(jobs) else 0
        if failures >= 3:
            print("three rounds where every agent call failed - stopping. The room is saved; fix the agent command "
                  "and run again (it resumes where it stopped).")
            sys.exit(2)
        try:
            res = r.submit()
            print("   ", json.dumps(res)[:600])
        except RoomError as e:
            if "no complete replies" not in str(e):
                raise
        rounds += 1
        r = Room(a.room)
    print()
    print(r.status())


def cmd_parallel(a):
    r = Room(a.room)
    r.s["parallel"] = {"candidates": a.candidates}
    r.save()
    print(f"candidates per turn set to {a.candidates} (takes effect from the next new turn).")


def cmd_record(a):
    rec.build(Room(a.room))


def cmd_retag(a):
    r = Room(a.room)
    r.start_retag()
    print(r.instructions())


def cmd_render(a):
    from moekit import audio
    audio.render(Room(a.room), mode=a.mode, temperature=a.temperature, top_p=a.top_p, takes=a.takes,
                 max_chars=a.max_chars, script=a.script, dry_run=a.dry_run, tts_cmd=a.tts_cmd)


def cmd_split(a):
    from moekit import audio
    r = Room(a.room)
    if a.mode == "turn":
        d = os.path.join(r.path, "render-turn")
        man = json.load(open(os.path.join(d, "manifest.json")))
        t = os.path.join(r.path, "turns")
        shutil.rmtree(t, ignore_errors=True)
        os.makedirs(t)
        for e in man["units"]:
            src = os.path.join(d, f"U{e['unit']:02d}-take{e.get('chosen', 1)}.wav")
            shutil.copy(src, os.path.join(t, f"T{e['turns'][0]:02d}.wav"))
        print(f"copied {len(man['units'])} turn renders to {t}\nNext: `moe.py mix {r.path}`")
    else:
        audio.split(r, mode="scene")


def cmd_mix(a):
    from moekit import audio
    audio.mix(Room(a.room))


def cmd_report(a):
    from moekit import audio
    audio.report(Room(a.room))


def cmd_probe(a):
    from moekit import audio
    src = a.target
    if os.path.isdir(src):
        cast = json.load(open(os.path.join(src, "state.json"), encoding="utf-8"))["config"]["cast"]
        out = os.path.join(src, "probe")
    else:
        cast = json.load(open(src, encoding="utf-8"))["cast"]
        out = os.path.join(os.path.dirname(os.path.abspath(src)), "probe")
    voices = {c["name"]: c.get("voice") for c in cast}
    missing = [n for n, v in voices.items() if not v]
    if missing:
        print(f"STOP: no voice id yet for {', '.join(missing)} - cast them first (SKILL.md section 5).")
        sys.exit(1)
    sys.exit(0 if audio.probe(voices, out) else 1)


def cmd_episode(a):
    from moekit import audio
    audio.episode(a.config)


def cmd_cast_brief(a):
    from moekit import casting
    casting.brief(a.dir, a.name, a.full_name, a.dossier)


def cmd_cast_submit(a):
    from moekit import casting
    casting.submit(a.dir)


def cmd_cast_design(a):
    from moekit import casting
    casting.design(a.dir, a.n, a.reference)


def cmd_cast_check(a):
    from moekit import casting
    casting.check(a.dir, a.compare)


def cmd_doctor(a):
    ok = True
    for exe in ("ffmpeg", "ffprobe"):
        if not shutil.which(exe):
            print(f"MISSING: {exe} on PATH"); ok = False
    for mod in ("numpy", "scipy", "requests"):
        try:
            __import__(mod)
        except ImportError:
            print(f"MISSING: python module {mod} (pip install {mod})"); ok = False
    print("FISH_API_KEY:", "set" if os.environ.get("FISH_API_KEY") else "not set (needed only for render)")
    # The prompt cards (prompts/_speech-card.md etc.) restate rules from the governing documents.
    # SOURCES.json pins the versions they were written against; a changed document means a
    # ruling may have moved, and the cards must be checked before the next room.
    src = os.path.join(KIT, "SOURCES.json")
    docs = os.path.abspath(a.docs or os.path.join(KIT, ".."))
    if os.path.exists(src):
        import hashlib
        pinned = json.load(open(src))
        current = {}
        for name, info in pinned.items():
            q = os.path.join(docs, name)
            if not os.path.exists(q):
                print(f"MISSING governing doc: {q} (pass --docs DIR if your documents live elsewhere)"); ok = False
                continue
            current[name] = hashlib.sha256(open(q, "rb").read()).hexdigest()
            if current[name] != info["sha256"]:
                last_f = info.get("last_feedback")
                print(f"CHANGED since the prompt cards were written: {name}"
                      + (f" (cards written through {last_f})" if last_f else "")
                      + "\n   -> read what changed. If it changes a rule a speaker, watcher or auditor must follow,"
                        "\n      update prompts/ to match (or ask the producer). Then run: moe.py doctor --accept")
                ok = False
                if name == "PRODUCER-NOTES.md" and last_f:
                    import re as _re
                    text = open(q, encoding="utf-8").read()
                    entries = _re.findall(r"^- \*\*(F-\d+)[^\n]*", text, _re.M)
                    ids = [e for e in _re.findall(r"^- \*\*(F-\d+)", text, _re.M)]
                    lines = _re.findall(r"^- \*\*F-\d+[^\n]*", text, _re.M)
                    seen = False
                    new = []
                    for fid, line in zip(ids, lines):
                        if seen and fid != last_f:
                            new.append(line[:170])
                        if fid == last_f:
                            seen = True
                    if new:
                        print("   New feedback entries since then - read each; only a RULING that changes what a\n"
                              "   speaker, watcher or auditor must do needs a prompt change:")
                        for ln in new:
                            print("     " + ln)
                    else:
                        print(f"   No new F-entries after {last_f}: an existing entry was edited - read the change.")
        if a.accept:
            import re as _re
            for name in current:
                pinned[name]["sha256"] = current[name]
                if name == "PRODUCER-NOTES.md":
                    fs = _re.findall(r"\*\*(F-\d+)", open(os.path.join(docs, name), encoding="utf-8").read())
                    pinned[name]["last_feedback"] = fs[-1] if fs else None
            json.dump(pinned, open(src, "w"), indent=1)
            print("SOURCES.json updated - you are confirming the prompt cards match the current documents.")
            ok = True
    print("OK" if ok else "NOT OK - fix the above before running a room.")


def main():
    p = argparse.ArgumentParser(prog="moe.py", add_help=True)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("help")
    for name, fn in (("next", cmd_next), ("status", cmd_status), ("reissue", cmd_reissue), ("record", cmd_record),
                     ("retag", cmd_retag), ("mix", cmd_mix), ("report", cmd_report)):
        s = sub.add_parser(name); s.add_argument("room"); s.set_defaults(fn=fn)
    s = sub.add_parser("new"); s.add_argument("room"); s.add_argument("--config", required=True); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("parallel"); s.add_argument("room"); s.add_argument("--candidates", type=int, required=True,
                                                                           choices=[1, 2, 3, 4])
    s.set_defaults(fn=cmd_parallel)
    s = sub.add_parser("import"); s.add_argument("room"); s.add_argument("--config", required=True)
    s.add_argument("--transcript", required=True); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("submit"); s.add_argument("room"); s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("run"); s.add_argument("room"); s.add_argument("--agent", required=True)
    s.add_argument("--max-steps", type=int, default=400); s.add_argument("--timeout", type=int, default=900)
    s.add_argument("--jobs", type=int, default=1, help="agent calls allowed at the same time (example: 5)")
    s.add_argument("--cwd", help="folder to run the agent command in (use an empty folder so agents see no project files)")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("render"); s.add_argument("room"); s.add_argument("--mode", choices=["scene", "turn"], default="scene")
    s.add_argument("--temperature", type=float, default=0.8); s.add_argument("--top-p", type=float, default=0.8)
    s.add_argument("--takes", type=int, default=1); s.add_argument("--max-chars", type=int, default=1400)
    s.add_argument("--script", choices=["committed", "retagged"], default="committed")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--tts-cmd", help="turn mode only: shell template with {text_file} {out} {voice}")
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("split"); s.add_argument("room"); s.add_argument("--mode", choices=["scene", "turn"], default="scene"); s.set_defaults(fn=cmd_split)
    s = sub.add_parser("probe"); s.add_argument("target", help="a room folder or a room.json"); s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("episode"); s.add_argument("config"); s.set_defaults(fn=cmd_episode)
    s = sub.add_parser("cast-brief"); s.add_argument("dir"); s.add_argument("--name", required=True)
    s.add_argument("--full-name", required=True); s.add_argument("--dossier", required=True); s.set_defaults(fn=cmd_cast_brief)
    s = sub.add_parser("cast-submit"); s.add_argument("dir"); s.set_defaults(fn=cmd_cast_submit)
    s = sub.add_parser("cast-design"); s.add_argument("dir"); s.add_argument("--n", type=int, default=4, choices=[1, 2, 3, 4])
    s.add_argument("--reference", choices=["quick", "weighty"], default="quick"); s.set_defaults(fn=cmd_cast_design)
    s = sub.add_parser("cast-check"); s.add_argument("dir"); s.add_argument("--compare", help="a wav/mp3 of another cast voice")
    s.set_defaults(fn=cmd_cast_check)
    s = sub.add_parser("doctor"); s.add_argument("--accept", action="store_true")
    s.add_argument("--docs", help="folder holding the governing documents (default: the kit's parent folder)")
    s.set_defaults(fn=cmd_doctor)
    a = p.parse_args()
    if not a.cmd or a.cmd == "help":
        print(HELP); return
    try:
        a.fn(a)
    except RoomError as e:
        print(f"STOP: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
