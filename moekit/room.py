"""The room: a state machine that owns every mechanical step of a conversation run.

The operator (the operator) never assembles context, never decides who speaks, never
counts words, never copies text between files. They run `moe.py next`, give
the printed prompt file to a FRESH agent, save the reply where told, and run
`moe.py submit`. The kit refuses out-of-order steps.

Flow per turn (PIPELINE.md, producer rulings):
  propose -> [format repair x1] -> watch -> PASS: commit
                                        -> OBJECT: restate -> [repair x1] -> recheck -> commit (no second veto)
After the close: audit (independent auditor) -> phase 'closed'.
"""
import datetime as dt
import hashlib
import json
import os
import re

from . import lint

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS = os.path.join(KIT, "prompts")
CLOSE_RESERVE = 70  # words left in the budget when the host is asked to close


# ---------------------------------------------------------------- utilities
def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fsha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read(path):
    return open(path, encoding="utf-8").read()


def write(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def norm(s):
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


class RoomError(Exception):
    pass


# ---------------------------------------------------------------- reply parsing
FIELD = re.compile(r"^\s*[*_`#>\s]*([A-Z_]{3,14})[*_`\s]*:\s*(.*)$")


def parse_fields(reply, first_field):
    """Tolerant 'KEY: value' parser. Value runs until the next known KEY line."""
    lines = reply.replace("\r\n", "\n").split("\n")
    out, key = {}, None
    for ln in lines:
        m = FIELD.match(ln)
        if m and m.group(1) in KNOWN_KEYS:
            key = m.group(1)
            out[key] = m.group(2).strip()
        elif key:
            out[key] += "\n" + ln
    out = {k: v.strip().strip("`").strip() for k, v in out.items()}
    if first_field not in out:
        return None
    return out


KNOWN_KEYS = {"TURN", "REASON", "TOPIC", "FRICTION", "RULING", "CHECK", "PHRASE", "WITHOUT", "LOSES", "WHY",
              "VERDICT", "SUMMARY", "INSTRUCTION", "QUICK_LINE", "WEIGHTY_LINE"}


DECLINE = re.compile(r"\b(I'?m not going to|I am not going to|I won'?t|I can'?t (help|do|play|voice)|"
                     r"I'?m not comfortable|I (must|have to) decline|I'?d rather not)\b", re.I)


def clean_turn(t):
    t = t.strip()
    if len(t) >= 2 and t[0] in "\"'“" and t[-1] in "\"'”":
        t = t[1:-1].strip()
    return re.sub(r"\s+", " ", t)


def unquote(s):
    s = (s or "").strip()
    if len(s) >= 2 and s[0] in "\"'“" and s[-1] in "\"'”":
        s = s[1:-1]
    return s.strip()


def is_subsequence(small, big):
    it = iter(big)
    return all(w in it for w in small)


def validate_objection(f, proposal):
    """Returns list of problems; empty = valid. Enforces: the phrase is really in the
    proposal, and WITHOUT (if given) only deletes - the watcher may never supply wording."""
    probs = []
    check = (f.get("CHECK") or "").strip().lower().replace("_", "-")
    if check not in {"deletion", "structure", "contingency", "read-aloud", "tag", "fact"}:
        probs.append("CHECK must be one of: deletion, structure, contingency, read-aloud, tag, fact.")
    phrase = unquote(f.get("PHRASE"))
    if not phrase:
        probs.append("PHRASE is missing.")
    elif norm(phrase) not in norm(proposal) and norm(phrase) not in norm(lint.strip_tags(proposal)):
        probs.append(f"PHRASE \"{phrase[:80]}\" is not copied exactly from the proposed turn.")
    without = unquote(f.get("WITHOUT"))
    if without and without.lower() != "none":
        w_small = norm(lint.strip_tags(without)).replace('"', "").split()
        w_big = norm(lint.strip_tags(proposal)).replace('"', "").split()
        strip_p = lambda ws: [re.sub(r"[^\w']", "", w) for w in ws]
        if not is_subsequence([w for w in strip_p(w_small) if w], [w for w in strip_p(w_big) if w]):
            probs.append("WITHOUT must be the proposed turn with PHRASE deleted - it may not add or change words.")
    if not (f.get("WHY") or "").strip():
        probs.append("WHY is missing.")
    if check == "deletion" and phrase and len(phrase.split()) <= 2 and \
            all(re.sub(r"[^\w']", "", w).lower() in DISCOURSE for w in phrase.split()):
        probs.append(f"\"{phrase}\" is a spoken connective - the read-aloud rule keeps these (producer ruling). "
                     "Object to something that matters, or rule PASS.")
    return probs


DISCOURSE = {"so", "then", "well", "right", "now", "look", "and", "but", "yes", "yeah", "no", "okay", "ok", "oh",
             "huh", "hm", "mm", "sure", "fine", "fair", "see", "i", "mean", "you", "know", "anyway", "still"}


# ---------------------------------------------------------------- the room
class Room:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.state_file = os.path.join(self.path, "state.json")
        if not os.path.exists(self.state_file):
            raise RoomError(f"No room at {self.path} (state.json missing). Create one with: moe.py new {path}")
        self.s = json.load(open(self.state_file, encoding="utf-8"))
        self.cfg = self.s["config"]

    # ---------- creation
    @staticmethod
    def create(path, config_file):
        path = os.path.abspath(path)
        cfg = json.load(open(config_file, encoding="utf-8"))
        base = os.path.dirname(os.path.abspath(config_file))
        problems = []
        for key in ("title", "cast", "occasion_file", "run_of_show", "standards"):
            if key not in cfg:
                problems.append(f"room.json is missing '{key}'")
        if problems:
            raise RoomError("; ".join(problems))
        hosts = [c for c in cfg["cast"] if c.get("role") == "host"]
        guests = [c for c in cfg["cast"] if c.get("role") == "guest"]
        if len(hosts) != 1 or not guests:
            if not (len(hosts) == 1 and cfg.get("run_of_show", {}).get("solo_brief")):
                raise RoomError("cast needs exactly one host and at least one guest "
                                "(or run_of_show.solo_brief for a host-only segment)")
        if len(guests) > 3:
            raise RoomError("at most three guests at one table (FORMAT.md voice ceiling: four voices incl. host)")
        names = [c["name"] for c in cfg["cast"]]
        if len(set(names)) != len(names):
            raise RoomError("cast short names must be unique")

        def resolve(p):
            p = os.path.expanduser(p)  # "~/podcasts/guests/x.md" works too
            q = p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))
            if not os.path.exists(q):
                problems.append(f"file not found: {p} (resolved {q})")
            return q

        for c in cfg["cast"]:
            c["files"] = [resolve(p) for p in c["files"]]
            c.setdefault("full_name", c["name"])
        cfg["occasion_file"] = resolve(cfg["occasion_file"])
        cfg["prior_rooms"] = [resolve(p) for p in cfg.get("prior_rooms", [])]
        for p in cfg["prior_rooms"]:
            if not os.path.exists(os.path.join(p, "TRANSCRIPT.md")):
                problems.append(f"prior room has no TRANSCRIPT.md: {p}")
        for k in ("avoid_list", "presentation"):
            cfg["standards"][k] = resolve(cfg["standards"][k])
        ros = cfg["run_of_show"]
        for k in (("word_budget",) if ros.get("solo_brief") else ("topics", "friction", "word_budget")):
            if k not in ros:
                problems.append(f"run_of_show is missing '{k}'")
        if problems:
            raise RoomError("; ".join(problems))
        ros.setdefault("hard_stop", int(ros["word_budget"] * 1.2))
        ros.setdefault("opening_note", "")
        sources = {}
        for c in cfg["cast"]:
            for p in c["files"]:
                sources[p] = fsha(p)
        for p in (cfg["occasion_file"], cfg["standards"]["avoid_list"], cfg["standards"]["presentation"]):
            sources[p] = fsha(p)
        for p in cfg["prior_rooms"]:
            sources[os.path.join(p, "TRANSCRIPT.md")] = fsha(os.path.join(p, "TRANSCRIPT.md"))
        if os.path.exists(os.path.join(path, "state.json")):
            raise RoomError(f"{path} already has a room (state.json). Use a new folder.")
        os.makedirs(os.path.join(path, "work"), exist_ok=True)
        cand = int(cfg.get("parallel", {}).get("candidates", 1))
        if not 1 <= cand <= 4:
            raise RoomError("parallel.candidates must be 1-4")
        state = {"kit_version": 2, "created": now(), "config": cfg, "sources": sources, "phase": "room",
                 "turns": [], "passes": [], "actions": [], "seq": 0, "pending": None, "side": [],
                 "parallel": {"candidates": cand}, "audit": None, "retag": None}
        json.dump(state, open(os.path.join(path, "state.json"), "w", encoding="utf-8"), indent=1)
        r = Room(path)
        r._schedule_next_turn()
        r.save()
        return r

    @staticmethod
    def import_transcript(path, config_file, transcript_file):
        """Bring an already-committed room (e.g. an earlier sample run) into the kit as a CLOSED room,
        so it can be re-tagged (performance-only), rendered, mixed and recorded with kit tools.
        The words are taken verbatim; nothing is audited again."""
        text = read(transcript_file)
        found = re.findall(r"\*\*T(\d+) · ([^:*]+):\*\* (.+?)(?=\n\*\*T\d+ |\Z)", text, re.S)
        if not found:
            raise RoomError(f"no '**T1 · Name:** text' turns found in {transcript_file}")
        r = Room.create(path, config_file)
        names = {c["name"] for c in r.cfg["cast"]}
        r.s["turns"] = []
        for n, spk, body in found:
            spk = spk.strip()
            if spk not in names:
                raise RoomError(f"T{n}: speaker '{spk}' is not in the room's cast {sorted(names)}")
            body = " ".join(body.split())
            r.s["turns"].append({"n": int(n), "speaker": spk, "role": r.member(spk)["role"], "text": body,
                                 "words": lint.words(body), "topic": None, "friction": False, "reason": None,
                                 "closing": False, "lint_override": False,
                                 "history": {"first_proposal": body, "first_ruling": {"ruling": "IMPORTED", "objection": None,
                                                                                      "note": f"imported from {os.path.basename(transcript_file)}"}},
                                 "committed_at": now()})
        r.s["turns"].sort(key=lambda t: t["n"])
        r.s["sources"][os.path.abspath(transcript_file)] = fsha(transcript_file)
        r.s["imported_from"] = os.path.abspath(transcript_file)
        r.s["phase"] = "closed"
        r.s["pending"] = None
        r.s["seq"] = 0
        for f in os.listdir(os.path.join(r.path, "work")):
            os.remove(os.path.join(r.path, "work", f))
        r.save()
        return r

    def save(self):
        tmp = self.state_file + ".tmp"
        json.dump(self.s, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        os.replace(tmp, self.state_file)
        self._write_records()

    # ---------- cast helpers
    def member(self, name):
        for c in self.cfg["cast"]:
            if c["name"] == name:
                return c
        raise RoomError(f"unknown speaker {name}")

    @property
    def host(self):
        return [c for c in self.cfg["cast"] if c["role"] == "host"][0]["name"]

    @property
    def guests(self):
        return [c["name"] for c in self.cfg["cast"] if c["role"] == "guest"]

    def committed(self):
        return [{"speaker": t["speaker"], "role": t["role"], "text": t["text"]} for t in self.s["turns"]]

    def words_so_far(self):
        return sum(lint.words(t["text"]) for t in self.s["turns"])

    # ---------- mediation (deterministic; producer ruling name-only offers are the host's words, not the kit's)
    def addressed_in(self, text, speaker):
        plain = lint.strip_tags(text)
        ss = lint.sentences(plain) or [plain]
        tail = " ".join(ss[-2:])
        best, pos = None, -1
        for c in self.cfg["cast"]:
            if c["name"] == speaker:
                continue
            for alias in {c["name"], c["full_name"].split()[0]}:
                for m in re.finditer(rf"\b{re.escape(alias)}\b", tail):
                    if m.start() > pos:
                        best, pos = c["name"], m.start()
        return best

    def next_speaker(self):
        turns = self.s["turns"]
        if not self.guests:
            return self.host, "solo"
        if not turns:
            return self.host, "opening"
        if self.s.get("closing"):
            return self.host, "closing"
        last = turns[-1]
        if len(self.guests) == 1:
            return (self.guests[0] if last["role"] == "host" else self.host), "normal"
        if last["role"] == "host":
            g = self.addressed_in(last["text"], last["speaker"])
            if g in self.guests:
                return g, "addressed"
            gw = {g: 0 for g in self.guests}
            for t in turns:
                if t["speaker"] in gw:
                    gw[t["speaker"]] += lint.words(t["text"])
            return min(self.guests, key=lambda g: (gw[g], self.guests.index(g))), "rotation"
        # Panels (producer ruling table budgets: host <= ~30% of turns, guests pick up from each other):
        # a guest who is addressed by name gets the floor; otherwise the floor opens to the guest who
        # has said least (not the one who just spoke), up to three guest turns in a row; then the host.
        run = 0
        for t in reversed(turns):
            if t["role"] == "guest":
                run += 1
            else:
                break
        g = self.addressed_in(last["text"], last["speaker"])
        if g in self.guests and run < 4:
            return g, "addressed"
        if run < 3:
            gw = {x: 0 for x in self.guests if x != last["speaker"]}
            for t in turns:
                if t["speaker"] in gw:
                    gw[t["speaker"]] += lint.words(t["text"])
            return min(gw, key=lambda x: (gw[x], self.guests.index(x))), "open-floor"
        return self.host, "normal"

    def _schedule_next_turn(self, after_pass=None):
        if self.s["phase"] != "room":
            return
        if after_pass:
            speaker, why = self.host, "after-pass"
        else:
            speaker, why = self.next_speaker()
        role = self.member(speaker)["role"]
        cue = lint.length_cue(role, why, self.committed(), speaker)
        friction_due, move_to = False, None
        if role == "host" and why not in ("opening", "closing", "solo") and not self.cfg["run_of_show"].get("solo_brief"):
            done = any(t.get("friction") for t in self.s["turns"])
            friction_due = (not done) and self.words_so_far() >= 0.5 * self.cfg["run_of_show"]["word_budget"]
            if not friction_due:
                move_to = self.topic_move_due()
        self._new_action("propose", speaker, len(self.s["turns"]) + 1, mode=why, cue=cue,
                         friction_due=friction_due, move_to=move_to)

    def topic_move_due(self):
        """If the room has spent well over its per-topic share on the furthest topic reached, return the
        number of the next topic (1-based). Uses the host's own TOPIC labels; never chooses words."""
        ros = self.cfg["run_of_show"]
        n = len(ros.get("topics", []))
        if n < 2:
            return None
        labelled = [(i, t["topic"]) for i, t in enumerate(self.s["turns"]) if t["role"] == "host" and t.get("topic")]
        if not labelled:
            return None
        furthest = max(min(tp, n) for _, tp in labelled)
        if furthest >= n:
            return None
        start = min(i for i, tp in labelled if min(tp, n) == furthest)
        spent = sum(lint.words(t["text"]) for t in self.s["turns"][start:])
        share = ros["word_budget"] / n
        return furthest + 1 if spent > 1.3 * share else None

    def _new_action(self, kind, speaker, turn, attempt=0, side=False, **extra):
        """Issue an action. MAIN actions are the room's critical path (one at a time). SIDE actions
        (rechecks, re-tags) don't block it and can run at the same time as it.
        A main propose/restate can be issued as N COPIES of the same prompt to N fresh agents; the
        kit takes the first copy (in fixed order) that passes the code checks - a mechanical choice, never taste."""
        self.s["seq"] += 1
        seq = self.s["seq"]
        tag = f"{seq:03d}-{kind}-{speaker or 'room'}" + (f"-repair{attempt}" if attempt else "")
        a = {"seq": seq, "kind": kind, "speaker": speaker, "turn": turn, "attempt": attempt, "side": side,
             "prompt": f"work/{tag}.prompt.md", "reply": f"work/{tag}.reply.md", **extra}
        n = int(self.s.get("parallel", {}).get("candidates", 1))
        if kind in ("propose", "restate") and attempt == 0 and n > 1 and not side:
            a["copies"] = [f"work/{tag}.c{i}.reply.md" for i in range(1, n + 1)]
            a["reply"] = a["copies"][0]
        prompt = self.build_prompt(a)
        write(os.path.join(self.path, a["prompt"]), prompt)
        a["prompt_sha256"] = sha(prompt)
        if side:
            self.s.setdefault("side", []).append(a)
        else:
            self.s["pending"] = a

    # ---------- prompt building
    def _tpl(self, name):
        return read(os.path.join(PROMPTS, name))

    def _files_text(self, member):
        parts = []
        for p in member["files"]:
            parts.append(read(p).strip())
        return "\n\n---\n\n".join(parts)

    def prior_text(self):
        """Earlier rooms of the same episode, tags stripped - what listeners have already heard."""
        out = []
        for p in self.cfg.get("prior_rooms", []):
            t = read(os.path.join(p, "TRANSCRIPT.md"))
            body = re.findall(r"\*\*T(\d+) · ([^:*]+):\*\* (.+?)(?=\n\*\*T\d+ |\Z)", t, re.S)
            out.append(f"[earlier segment: {os.path.basename(p)}]\n" + "\n".join(
                f"{s.strip()}: {lint.strip_tags(' '.join(b.split()))}" for _, s, b in body))
        return "\n\n".join(out)

    def transcript_text(self, tags=False, upto=None):
        turns = self.s["turns"][:upto] if upto is not None else self.s["turns"]
        prior = self.prior_text()
        head = ("EARLIER IN THIS EPISODE (already heard by listeners - don't repeat it; build on it):\n"
                + prior + "\n\nTHIS SEGMENT:\n") if prior else ""
        if not turns:
            return head + "(nothing has been said yet)"
        out = []
        for t in turns:
            txt = t["text"] if tags else lint.strip_tags(t["text"])
            out.append(f"T{t['n']} {t['speaker']}: {txt}")
        return head + "\n\n".join(out)

    def run_of_show_text(self):
        ros = self.cfg["run_of_show"]
        if ros.get("solo_brief"):
            return f"Segment brief: {ros['solo_brief']}" + (f"\nNotes: {ros['notes']}" if ros.get("notes") else "")
        lines = ["Topics (where the conversation can go, in rough order):"]
        for i, t in enumerate(ros["topics"], 1):
            lines.append(f"  {i}. {t}")
        lines.append(f"Booked friction - the one point where you genuinely disagree or bring a counter-case, "
                     f"at some point when it fits: {ros['friction']}")
        if ros.get("notes"):
            lines.append(f"Notes: {ros['notes']}")
        lines.append(f"Length: about {ros['word_budget']} spoken words in total (about five minutes per 750).")
        return "\n".join(lines)

    def floor_note(self, a):
        mode = a.get("mode")
        turns = self.s["turns"]
        sp = a["speaker"]
        if mode == "solo":
            return ("This is a host-only segment: you speak alone, to the listeners. "
                    + self.cfg["run_of_show"]["solo_brief"]
                    + f" About {self.cfg['run_of_show']['word_budget']} words.")
        if mode == "opening":
            note = self.cfg["run_of_show"].get("opening_note") or ""
            return ("You open the conversation. In two or three plain sentences: who's here and what we're "
                    "talking about, then your first question to a guest by name. " + note).strip()
        if mode == "closing":
            return ("This is your CLOSING turn. Say only what was actually agreed and what stayed split, "
                    "each attributed to whoever said it - no new points, no slogan. Then thank the guest"
                    f"{'s' if len(self.guests) > 1 else ''} by name. Seventy words at most.")
        if not turns:
            return "The floor is yours."
        last = turns[-1]
        said = lint.strip_tags(last["text"])
        if mode == "after-pass":
            return f"{self.s['passes'][-1]['speaker']} passed. The last thing said was {last['speaker']}: \"{said}\""
        if mode == "addressed":
            return f"{last['speaker']} just spoke to you: \"{said}\""
        if mode == "open-floor":
            return (f"{last['speaker']} just finished: \"{said}\"\nThe floor is open to you. If you have something "
                    f"to say to {last['speaker']} - agree, push back, or add what they missed - say it to them "
                    "directly. If you'd genuinely let it pass, reply TURN: PASS.")
        return f"{last['speaker']} just finished: \"{said}\""

    def _cue_lines(self, a):
        out = []
        if a.get("cue") in lint.LENGTH_CUES:
            out.append(lint.LENGTH_CUES[a["cue"]][1])
        if a.get("friction_due"):
            out.append("THIS TURN - THE BOOKED FRICTION IS DUE: push back now, honestly, on this point: "
                       + self.cfg["run_of_show"]["friction"] + " Then add the line FRICTION: yes.")
        if a.get("move_to"):
            k = a["move_to"]
            out.append(f"THIS TURN - MOVE ON: this thread has had its share of the time. Close it in a few words "
                       f"if you like, then take the conversation to topic {k}: "
                       f"{self.cfg['run_of_show']['topics'][k - 1]}. Mark TOPIC: {k}.")
        return out

    def pacing_note(self):
        ros = self.cfg["run_of_show"]
        used = self.words_so_far()
        if ros.get("solo_brief"):
            return f"Length: about {ros['word_budget']} words."
        topics = [t.get("topic") for t in self.s["turns"] if t["role"] == "host" and t.get("topic")]
        fr = [t["n"] for t in self.s["turns"] if t.get("friction")]
        n = len(ros["topics"])
        lines = [f"Pacing: {used} of about {ros['word_budget']} words used; {n} topics in the run-of-show, "
                 f"about {ros['word_budget'] // max(1, n)} words each."]
        if topics:
            lines.append(f"You've been on topic(s): {', '.join(str(t) for t in topics[-6:])} (most recent last).")
        lines.append("Booked friction: " + (f"done (turn T{fr[0]})." if fr else "NOT yet done - fit it in where it's honest."))
        return "\n".join(lines)

    def build_prompt(self, a):
        kind, sp = a["kind"], a["speaker"]
        fills = {"{{SPEECH_CARD}}": self._tpl("_speech-card.md").strip(),
                 "{{PERFORMANCE_CARD}}": self._tpl("_performance-card.md").strip(),
                 "{{OCCASION}}": read(self.cfg["occasion_file"]).strip(),
                 "{{HOST}}": self.member(self.host)["full_name"],
                 "{{GUESTS}}": ", ".join(self.member(g)["full_name"] for g in self.guests) or "the listeners (a solo segment)"}
        if kind in ("propose", "restate"):
            m = self.member(sp)
            tpl = self._tpl("host-turn.md" if m["role"] == "host" else "guest-turn.md")
            fills.update({"{{FULL_NAME}}": m["full_name"], "{{DOSSIER}}": self._files_text(m),
                          "{{TRANSCRIPT}}": self.transcript_text(),
                          "{{FLOOR_NOTE}}": self.floor_note(a if kind == "propose" else self._propose_ctx(a)),
                          "{{ROOM_NOTES}}": "\n".join(self._cue_lines(a) + lint.room_notes_for(sp, self.committed(),
                                                                         self.cfg["run_of_show"]["word_budget"])),
                          "{{RUN_OF_SHOW}}": self.run_of_show_text(), "{{PACING_NOTE}}": self.pacing_note()})
            block = ""
            if kind == "restate":
                o = a["objection"]
                block = ("=== YOUR PROPOSED TURN WAS RETURNED BY THE REVIEWER ===\n"
                         f"You proposed: {a['proposal']}\n"
                         f"The objection ({o.get('CHECK', '')}): {o.get('WHY', '')}\n"
                         f"The words it flagged: \"{unquote(o.get('PHRASE'))}\"\n"
                         "Say your turn again, once, in your own words, fixing that. Don't mention the "
                         "objection. Keep everything else you meant to say.\n")
            if a["attempt"]:
                block += self._repair_block(a)
            fills["{{RESTATE_BLOCK}}"] = block
        elif kind in ("watch", "recheck"):
            m = self.member(sp)
            text = a["proposal"]
            facts = a.get("lint", {})
            fl = [f"words: {facts.get('facts', {}).get('words')}",
                  f"shape: {', '.join(facts.get('facts', {}).get('shape', [])) or 'none detected'}",
                  f"delivery directions: {facts.get('facts', {}).get('directions') or 'none'}",
                  f"sound marks: {facts.get('facts', {}).get('point_tags') or 'none'}"]
            fl += [f"NOTE: {h}" for h in facts.get("hints", [])]
            fl += [f"FORMAT (already repaired once, still present): {h}" for h in facts.get("hard", [])]
            fills.update({"{{TRANSCRIPT}}": self.transcript_text(), "{{SPEAKER}}": m["full_name"],
                          "{{OCCASION}}": read(self.cfg["occasion_file"]).strip(),
                          "{{ROLE}}": m["role"], "{{PROPOSAL}}": text, "{{FACTS}}": "\n".join(fl),
                          "{{AVOID_LIST}}": read(self.cfg["standards"]["avoid_list"]).strip(),
                          "{{PRESENTATION}}": read(self.cfg["standards"]["presentation"]).strip(),
                          "{{RECHECK_NOTE}}": ("\n(This is a RESTATEMENT after an earlier objection. Rule on it "
                                               "fresh; it commits whatever you rule - your ruling goes on record.)\n"
                                               if kind == "recheck" else "")})
            tpl = self._tpl("watcher.md")
            if a["attempt"]:
                tpl += "\n" + self._repair_block(a)
        elif kind == "audit":
            st = lint.room_stats(self.committed())
            facts = [f"turns: {st['turns']}, words: {st['words']}, turns of ten words or fewer: {st['short_turns']}",
                     f"guest median turn: {st['guest_median_words']} words; contractions per 100 words: "
                     f"{st['contractions_per_100w']}"]
            for spk, p in st["per_speaker"].items():
                facts.append(f"{spk}: turn lengths {p['words']}; shapes {p['shapes'] or '{}'}; delivery cells "
                             f"{p['cells']}; sound marks {p['points']}, directions {p['directions']}")
            fills.update({"{{OCCASION}}": read(self.cfg["occasion_file"]).strip(),
                          "{{TRANSCRIPT}}": self.transcript_text(tags=True), "{{FACTS}}": "\n".join(facts),
                          "{{AVOID_LIST}}": read(self.cfg["standards"]["avoid_list"]).strip(),
                          "{{PRESENTATION}}": read(self.cfg["standards"]["presentation"]).strip()})
            tpl = self._tpl("auditor.md")
            if a["attempt"]:
                tpl += "\n" + self._repair_block(a)
        elif kind == "retag":
            m = self.member(sp)
            t = self.s["turns"][a["turn"] - 1]
            fills.update({"{{FULL_NAME}}": m["full_name"], "{{DOSSIER}}": self._files_text(m),
                          "{{TRANSCRIPT}}": self.transcript_text(),
                          "{{TURN_NO}}": f"T{t['n']}", "{{WORDS}}": lint.strip_tags(t["text"]),
                          "{{ROOM_NOTES}}": ""})
            tpl = self._tpl("retag.md")
            if a["attempt"]:
                tpl += "\n" + self._repair_block(a)
        else:
            raise RoomError(f"unknown action kind {kind}")
        for k, v in fills.items():
            tpl = tpl.replace(k, v)
        left = re.findall(r"\{\{[A-Z_]+\}\}", tpl)
        if left:
            raise RoomError(f"prompt template left unfilled: {left}")
        return tpl

    def _propose_ctx(self, a):
        return {"speaker": a["speaker"], "mode": a.get("mode", "normal")}

    def _repair_block(self, a):
        return ("\n=== YOUR LAST REPLY COULD NOT BE USED ===\n"
                f"You replied:\n{a.get('bad_reply', '').strip()[:3000]}\n\nProblems:\n"
                + "\n".join(f"- {p}" for p in a.get("problems", []))
                + "\nReply again in the exact REPLY FORMAT, with the same meaning, fixing only these problems.\n")

    # ---------- the operator loop
    def outstanding(self):
        """Every action an agent can be working on right now: [(action, [reply paths still missing])]."""
        out = []
        for a in ([self.s["pending"]] if self.s["pending"] else []) + list(self.s.get("side", [])):
            paths = a.get("copies") or [a["reply"]]
            missing = [p for p in paths if not os.path.exists(os.path.join(self.path, p))]
            out.append((a, missing))
        return out

    def _describe(self, a):
        what = {"propose": f"{a['speaker']} proposes turn T{a['turn']}",
                "restate": f"{a['speaker']} restates turn T{a['turn']} after an objection",
                "watch": f"WATCHER audits {a['speaker']}'s proposed turn T{a['turn']}",
                "recheck": f"WATCHER re-checks {a['speaker']}'s restated turn T{a['turn']} (for the record)",
                "audit": "AUDITOR reads the finished room",
                "retag": f"{a['speaker']} re-tags the performance of turn T{a['turn']} (words fixed)"}[a["kind"]]
        if a["attempt"]:
            what += " - FORMAT REPAIR (the last reply was unusable)"
        return what

    def instructions(self):
        outs = self.outstanding()
        if not outs:
            if self.s["phase"] in ("closed",):
                return ("ROOM CLOSED - nothing to spawn. Next: `moe.py record ROOM`, then render "
                        "(`moe.py render ROOM`), `moe.py mix ROOM`, `moe.py report ROOM`.")
            return "Nothing outstanding. Run `moe.py status ROOM`."
        jobs = sum(len(m) for _, m in outs)
        lines = [f"NEXT: {jobs} agent job(s) can run AT THE SAME TIME (run up to your agent limit at once; "
                 f"submit after any of them finish)."]
        for a, missing in outs:
            lines.append(f"\n[#{a['seq']}] {self._describe(a)}" + ("   (side job - doesn't block the room)" if a.get("side") else ""))
            lines.append(f"   prompt: {os.path.join(self.path, a['prompt'])}")
            if a.get("copies"):
                lines.append(f"   {len(a['copies'])} COPIES: start {len(a['copies'])} separate FRESH agents, each given this "
                             "same prompt file. Save each one's reply to its own file:")
            for p in (a.get("copies") or [a["reply"]]):
                done = "" if p in missing else "   [saved]"
                lines.append(f"   reply -> {os.path.join(self.path, p)}{done}")
        lines.append(f"\nThen run:  python moe.py submit \"{self.path}\"")
        lines.append("Each agent: FRESH session, the prompt file as its only message, no tools. Save replies unedited. "
                     "Never reuse an agent across jobs.")
        return "\n".join(lines)

    def _check_prompt(self, a):
        if sha(read(os.path.join(self.path, a["prompt"]))) != a["prompt_sha256"]:
            raise RoomError(f"the prompt file {a['prompt']} was edited after it was issued - never edit prompts. "
                            "Run `moe.py reissue ROOM` and give the NEW prompt to NEW agents.")

    def _log(self, a, reply, extra=None):
        rec = {"seq": a["seq"], "kind": a["kind"], "speaker": a["speaker"], "turn": a["turn"],
               "attempt": a["attempt"], "side": a.get("side", False), "prompt": a["prompt"], "reply": a["reply"],
               "prompt_sha256": a["prompt_sha256"], "reply_sha256": sha(reply), "at": now()}
        rec.update(extra or {})
        self.s["actions"].append(rec)
        return rec

    def submit(self, reply_text=None):
        """Process every action whose reply file(s) are complete: side jobs first, then the main action.
        reply_text (single-job convenience for scripts): written to the main action's reply file first."""
        if reply_text is not None and self.s["pending"]:
            write(os.path.join(self.path, self.s["pending"]["reply"]), reply_text)
        results = []
        for a in list(self.s.get("side", [])):
            rp = os.path.join(self.path, a["reply"])
            if not os.path.exists(rp):
                continue
            reply = read(rp)
            if not reply.strip():
                raise RoomError(f"reply file is empty: {rp}")
            self._check_prompt(a)
            self.s["side"].remove(a)
            rec = self._log(a, reply)
            rec["result"] = getattr(self, "_on_" + a["kind"])(a, reply)
            results.append(rec["result"])
        a = self.s["pending"]
        if a:
            paths = a.get("copies") or [a["reply"]]
            present = [p for p in paths if os.path.exists(os.path.join(self.path, p))]
            if len(present) == len(paths):
                replies = [read(os.path.join(self.path, p)) for p in paths]
                if any(not r.strip() for r in replies):
                    raise RoomError("a reply file is empty - save the agent's whole reply")
                self._check_prompt(a)
                extra = {}
                reply = replies[0]
                if len(replies) > 1:
                    idx = self._choose_copy(a, replies)
                    reply = replies[idx]
                    extra = {"copies": len(replies), "chosen_copy": idx + 1,
                             "copy_sha256s": [sha(r) for r in replies]}
                    a = dict(a, reply=paths[idx])
                rec = self._log(a, reply, extra)
                rec["result"] = getattr(self, "_on_" + a["kind"])(a, reply)
                results.append(rec["result"])
        if not results:
            waiting = [p for _, m in self.outstanding() for p in m]
            self.save()
            raise RoomError("no complete replies to process yet. Still waiting for:\n  "
                            + "\n  ".join(os.path.join(self.path, p) for p in waiting))
        self._maybe_close()
        self.save()
        return results[-1] if len(results) == 1 else {"status": "batch", "results": results}

    def _choose_copy(self, a, replies):
        """First copy (fixed order) that the code checks accept; if none does, copy 1 (it then gets the
        normal repair). Declines are skipped; if EVERY copy declines, copy 1 is used and the decline STOP fires."""
        m = self.member(a["speaker"])
        names = [c["name"] for c in self.cfg["cast"]]
        for i, r in enumerate(replies):
            f = parse_fields(r, "TURN")
            if f is None:
                continue
            text = clean_turn(f["TURN"])
            if text.upper().rstrip(".") == "PASS":
                if m["role"] == "guest" and a["kind"] == "propose":
                    return i
                continue
            if not lint.check(text, a["speaker"], m["role"], names, self.committed(),
                              reason=f.get("REASON"), cue=a.get("cue"))["hard"]:
                return i
        return 0

    def _maybe_close(self):
        if self.s["phase"] == "finishing" and not self.s.get("side"):
            self.s["phase"] = "closed"
        if self.s["phase"] == "retag" and not self.s.get("side"):
            self.s["phase"] = "closed"
            write(os.path.join(self.path, "TRANSCRIPT-RETAGGED.md"), self._transcript_md(retag=True))

    # ---------- handlers
    def _speaker_reply(self, a, reply):
        f = parse_fields(reply, "TURN")
        if f is None:
            if DECLINE.search(reply[:600]):
                # An agent declining the role is not a format error: don't pressure it with a repair.
                raise RoomError(
                    f"the agent for {a['speaker']} DECLINED the role (reply: {a['reply']}). This is not a format "
                    "problem - do not retry with pressure or edit the prompt to get around it. Read the reply, "
                    "tell the producer what it said, and wait for his direction. (To try a fresh agent unchanged: "
                    f"moe.py reissue {self.path})")
            return None, ["The reply has no line starting with TURN:."], {}
        text = clean_turn(f["TURN"])
        return text, [], f

    def _retry_or(self, a, reply, problems, fallback):
        if a["attempt"] == 0:
            keep = {k: v for k, v in a.items() if k not in ("seq", "prompt", "reply", "prompt_sha256", "attempt",
                                                            "kind", "speaker", "turn", "problems", "bad_reply",
                                                            "side", "copies")}
            self._new_action(a["kind"], a["speaker"], a["turn"], attempt=1, side=a.get("side", False),
                             problems=problems, bad_reply=reply, **keep)
            return {"status": "repair-requested", "problems": problems}
        return fallback()

    def _on_propose(self, a, reply, restate=False):
        text, probs, f = self._speaker_reply(a, reply)
        m = self.member(a["speaker"])
        if text is not None and text.upper().rstrip(".") == "PASS":
            if m["role"] == "host":
                probs = ["The host can't pass - the host always speaks."]
            elif restate:
                probs = ["A restatement can't be PASS - say the turn again in your own words."]
            else:
                self.s["passes"].append({"speaker": a["speaker"], "before_turn": a["turn"], "at": now()})
                self.s["pending"] = None
                self._schedule_next_turn(after_pass=True)
                return {"status": "pass"}
        res = None
        if text is not None and not probs:
            res = lint.check(text, a["speaker"], m["role"], [c["name"] for c in self.cfg["cast"]], self.committed(),
                             reason=f.get("REASON"), cue=a.get("cue"))
            probs = res["hard"]
        if probs:
            def fallback():
                if text is None or not lint.strip_tags(text):
                    raise RoomError("two unusable replies in a row for this action. Check the agent is getting the "
                                    f"whole prompt file. Then run `moe.py reissue {self.path}` to try again.")
                # second failure: strip rule-breaking performance marks (words untouched), then re-check
                fixed, removed = lint.autofix_tags(text, a["speaker"], m["role"], self.committed())
                names = [c["name"] for c in self.cfg["cast"]]
                res2 = lint.check(fixed, a["speaker"], m["role"], names, self.committed(),
                                  reason=f.get("REASON"), cue=a.get("cue"))
                f2 = dict(f)
                f2["_autofix_removed"] = removed
                return self._to_watch(a, fixed, f2, res2, restate, lint_override=bool(res2["hard"]))
            return self._retry_or(a, reply, probs, fallback)
        return self._to_watch(a, text, f, res, restate)

    def _to_watch(self, a, text, f, res, restate, lint_override=False):
        meta = {"reason": f.get("REASON"), "topic": f.get("TOPIC"),
                "friction": (f.get("FRICTION") or "").strip().lower().startswith("y"),
                "autofix_removed": f.get("_autofix_removed") or []}
        extra = {"proposal": text, "meta": meta, "lint": res, "lint_override": lint_override, "mode": a.get("mode"),
                 "cue": a.get("cue"), "friction_due": a.get("friction_due", False),
                 "move_to": a.get("move_to")}
        if restate:
            # The restatement commits whatever the re-check says (no second veto, producer ruling), so the re-check is
            # issued as a SIDE job and the room moves on at once. Its prompt is built now, before the commit,
            # so it sees exactly the transcript the restatement answered.
            self._new_action("recheck", a["speaker"], a["turn"], side=True, original=a["proposal"],
                             objection=a["objection"], **extra)
            history = {"first_proposal": a["proposal"], "objection": a["objection"], "restated": text,
                       "recheck": {"ruling": "PENDING", "objection": None, "note": None}}
            self.s["pending"] = None
            self._commit(dict(a, lint_override=lint_override), text, meta, history)
            return {"status": "committed", "turn": a["turn"], "ruling": "restated", "recheck": "side-job"}
        self._new_action("watch", a["speaker"], a["turn"], **extra)
        return {"status": "to-watch", "words": lint.words(text), "hints": res["hints"] if res else []}

    def _on_restate(self, a, reply):
        return self._on_propose(a, reply, restate=True)

    def _on_watch(self, a, reply, recheck=False):
        f = parse_fields(reply, "RULING")
        probs = []
        if f is None:
            first = reply.strip().split()[0].upper().strip("*:") if reply.strip() else ""
            if first in ("PASS", "OBJECT"):
                f = {"RULING": first}
            else:
                probs = ["The reply has no line starting with RULING:."]
        ruling = (f or {}).get("RULING", "").split()[0].upper().strip("*.:") if f and f.get("RULING") else ""
        if not probs and ruling not in ("PASS", "OBJECT"):
            probs = ["RULING must be PASS or OBJECT."]
        if not probs and ruling == "OBJECT":
            probs = validate_objection(f, a["proposal"])
        if probs:
            def fallback():
                return self._after_ruling(a, "PASS", None, recheck,
                                          note="watcher reply invalid twice - recorded as PASS: " + "; ".join(probs))
            return self._retry_or(a, reply, probs, fallback)
        obj = {k: f.get(k) for k in ("CHECK", "PHRASE", "WITHOUT", "LOSES", "WHY")} if ruling == "OBJECT" else None
        return self._after_ruling(a, ruling, obj, recheck)

    def _on_recheck(self, a, reply):
        """Side job: the re-check's ruling goes on the record of the already-committed turn."""
        f = parse_fields(reply, "RULING")
        probs = []
        if f is None:
            first = reply.strip().split()[0].upper().strip("*:") if reply.strip() else ""
            f = {"RULING": first} if first in ("PASS", "OBJECT") else None
            if f is None:
                probs = ["The reply has no line starting with RULING:."]
        ruling = (f.get("RULING") or "").split()[0].upper().strip("*.:") if f and f.get("RULING") else ""
        if not probs and ruling not in ("PASS", "OBJECT"):
            probs = ["RULING must be PASS or OBJECT."]
        if not probs and ruling == "OBJECT":
            probs = validate_objection(f, a["proposal"])
        if probs and a["attempt"] == 0:
            return self._retry_or(a, reply, probs, None)
        turn = next(t for t in self.s["turns"] if t["n"] == a["turn"])
        if probs:
            turn["history"]["recheck"] = {"ruling": "PASS", "objection": None,
                                          "note": "watcher reply invalid twice - recorded as PASS: " + "; ".join(probs)}
        else:
            obj = {k: f.get(k) for k in ("CHECK", "PHRASE", "WITHOUT", "LOSES", "WHY")} if ruling == "OBJECT" else None
            turn["history"]["recheck"] = {"ruling": ruling, "objection": obj, "note": None}
        return {"status": "recheck-recorded", "turn": a["turn"], "ruling": turn["history"]["recheck"]["ruling"]}

    def _after_ruling(self, a, ruling, obj, recheck, note=None):
        if ruling == "OBJECT" and not recheck:
            self._new_action("restate", a["speaker"], a["turn"], proposal=a["proposal"], objection=obj,
                             cue=a.get("cue"), friction_due=a.get("friction_due", False), move_to=a.get("move_to"),
                             orig_meta=a.get("meta"), orig_lint=a.get("lint"), mode=a.get("mode"))
            return {"status": "objection", "check": obj.get("CHECK"), "phrase": unquote(obj.get("PHRASE"))}
        record = {"ruling": ruling, "objection": obj, "note": note}
        if recheck:
            commit_text, meta = a["proposal"], a.get("meta") or {}
            history = {"first_proposal": a["original"], "objection": a["objection"], "restated": a["proposal"],
                       "recheck": record}
        else:
            commit_text, meta = a["proposal"], a.get("meta") or {}
            history = {"first_proposal": a["proposal"], "first_ruling": record}
        self._commit(a, commit_text, meta, history)
        return {"status": "committed", "turn": a["turn"], "ruling": ruling, "recheck": recheck}

    def _commit(self, a, text, meta, history):
        m = self.member(a["speaker"])
        topic = meta.get("topic")
        try:
            topic = int(re.findall(r"\d+", str(topic))[0]) if topic else None
        except IndexError:
            topic = None
        closing = a.get("mode") in ("closing", "solo")
        turn = {"n": a["turn"], "speaker": a["speaker"], "role": m["role"], "text": text,
                "words": lint.words(text), "topic": topic, "friction": bool(meta.get("friction")),
                "reason": meta.get("reason"), "closing": closing, "lint_override": a.get("lint_override", False),
                "autofix_removed": meta.get("autofix_removed") or [],
                "history": history, "committed_at": now()}
        self.s["turns"].append(turn)
        self.s["pending"] = None
        if closing:
            self.s["phase"] = "audit"
            self._new_action("audit", None, None)
            return
        used = self.words_so_far()
        ros = self.cfg["run_of_show"]
        if used >= ros["word_budget"] - CLOSE_RESERVE or used >= ros["hard_stop"] or \
                len(self.s["turns"]) >= ros.get("max_turns", 60):
            nxt, _ = self.next_speaker()
            if nxt == self.host or used >= ros["hard_stop"]:
                self.s["closing"] = True
        self._schedule_next_turn()

    def _on_audit(self, a, reply):
        f = parse_fields(reply, "VERDICT")
        n = len(self.s["turns"])
        lines = {}
        for ln in reply.splitlines():
            m = re.match(r"^\s*[*_]*T(\d+)[*_]*\s*:\s*(CLEAN|FLAG)\b\s*(.*)$", ln.strip(), re.I)
            if m:
                lines[int(m.group(1))] = (m.group(2).upper(), m.group(3).strip())
        probs = []
        if f is None:
            probs.append("The reply has no VERDICT: line.")
        missing = [i for i in range(1, n + 1) if i not in lines]
        if missing:
            probs.append(f"Missing per-turn lines for: {', '.join('T' + str(i) for i in missing)}.")
        flags, bad = [], []
        for i, (kind, rest) in sorted(lines.items()):
            if kind == "FLAG" and 1 <= i <= n:
                q = re.search(r"\"([^\"]+)\"|“([^”]+)”", rest)
                phrase = (q.group(1) or q.group(2)) if q else ""
                ok = bool(phrase) and norm(phrase) in norm(self.s["turns"][i - 1]["text"])
                flags.append({"turn": i, "detail": rest, "phrase": phrase, "phrase_verified": ok})
                if not ok:
                    bad.append(f"T{i}")
        if bad:
            probs.append(f"Flag phrases not found verbatim in their turns: {', '.join(bad)}. Copy them exactly.")
        if probs and a["attempt"] == 0:
            return self._retry_or(a, reply, probs, None)
        self.s["audit"] = {"flags": flags, "summary": (f or {}).get("SUMMARY", ""), "problems_left": probs,
                           "reply": a["reply"], "at": now()}
        self.s["phase"] = "finishing" if self.s.get("side") else "closed"
        self.s["pending"] = None
        return {"status": "audited", "flags": len(flags), "unverified": bad}

    # ---------- retag (performance-only pass on committed words)
    def start_retag(self):
        if self.s["phase"] != "closed":
            raise RoomError("retag runs on a closed room (after the audit).")
        self.s["retag"] = {"started": now(), "done": [], "texts": {}}
        self.s["phase"] = "retag"
        # each re-tag sees the fixed words and its own turn only - they don't depend on each other,
        # so all are issued at once as side jobs
        for t in self.s["turns"]:
            self._new_action("retag", t["speaker"], t["n"], side=True)
        self.save()

    def _on_retag(self, a, reply):
        t = self.s["turns"][a["turn"] - 1]
        f = parse_fields(reply, "TURN")
        probs = []
        new = clean_turn(f["TURN"]) if f else None
        if new is None:
            probs = ["The reply has no line starting with TURN:."]
        else:
            if lint.strip_tags(new).split() != lint.strip_tags(t["text"]).split():
                probs.append("The words changed. Copy every word exactly - only add marks between them.")
            res = lint.check(new, t["speaker"], t["role"], [c["name"] for c in self.cfg["cast"]],
                             self.committed()[:a["turn"] - 1], reason="retag")
            probs += [h for h in res["hard"] if "fifty words" not in h]
        if probs:
            if a["attempt"] == 0:
                return self._retry_or(a, reply, probs, None)
            new = t["text"]
            note = "kept original tags: " + "; ".join(probs)
        else:
            note = None
        self.s["retag"]["texts"][str(a["turn"])] = {"text": new, "note": note}
        self.s["retag"]["done"].append(a["turn"])
        return {"status": "retagged", "turn": a["turn"], "note": note}

    def reissue(self):
        """Re-issue every outstanding job that has no reply yet (agent crashed, reply lost, prompt edited)."""
        outs = [(a, m) for a, m in self.outstanding() if m]
        if not outs:
            raise RoomError("nothing outstanding without a reply - run `moe.py submit ROOM`")
        for a, _ in outs:
            keep = {k: v for k, v in a.items() if k not in ("seq", "prompt", "reply", "prompt_sha256", "attempt",
                                                            "kind", "speaker", "turn", "problems", "bad_reply",
                                                            "side", "copies")}
            if a.get("side"):
                self.s["side"].remove(a)
            self._new_action(a["kind"], a["speaker"], a["turn"], side=a.get("side", False), **keep)
        self.save()

    # ---------- records (regenerated on every save; derived, never hand-edited)
    def _transcript_md(self, retag=False, delivered=False):
        title = self.cfg["title"]
        head = (f"# {title} - " + ("delivered transcript (tags stripped)" if delivered else
                                    "performance script, re-tagged" if retag else "committed transcript (performance script)")
                + "\n\n")
        out = []
        for t in self.s["turns"]:
            txt = t["text"]
            if retag and self.s.get("retag") and str(t["n"]) in self.s["retag"]["texts"]:
                txt = self.s["retag"]["texts"][str(t["n"])]["text"]
            if delivered:
                txt = lint.strip_tags(txt)
            out.append(f"**T{t['n']} · {t['speaker']}:** {txt}")
        return head + "\n\n".join(out) + "\n"

    def _write_records(self):
        write(os.path.join(self.path, "TRANSCRIPT.md"), self._transcript_md())
        write(os.path.join(self.path, "TRANSCRIPT-DELIVERED.md"), self._transcript_md(delivered=True))
        for c in self.cfg["cast"]:
            lines = [f"# Scratch - {c['full_name']}. Proposals + watcher rulings. Never enters any agent's context.\n"]
            for t in self.s["turns"]:
                if t["speaker"] != c["name"]:
                    continue
                h = t["history"]
                lines.append(f"## T{t['n']} PROPOSED\n{h['first_proposal']}")
                if "first_ruling" in h:
                    r = h["first_ruling"]
                    lines.append(f"Watcher T{t['n']}: {r['ruling']}" + (f" ({r['note']})" if r.get("note") else ""))
                else:
                    o = h["objection"]
                    lines.append(f"Watcher T{t['n']}: OBJECT [{o.get('CHECK')}] \"{unquote(o.get('PHRASE'))}\" - "
                                 f"{o.get('WHY')} (loses: {o.get('LOSES')})")
                    lines.append(f"## T{t['n']} v2 (restated)\n{h['restated']}")
                    r = h["recheck"]
                    lines.append(f"Watcher T{t['n']} re-check: {r['ruling']}"
                                 + (f" [{r['objection'].get('CHECK')}] \"{unquote(r['objection'].get('PHRASE'))}\" - recorded, not enforced"
                                    if r.get("objection") else "") + f" - T{t['n']} commits.")
                if t.get("reason"):
                    lines.append(f"Length reason (>{50} words): {t['reason']}")
                if t.get("lint_override"):
                    lines.append("NOTE: committed with a format problem after one repair (see state.json).")
                lines.append("")
            write(os.path.join(self.path, "scratch", f"{c['name'].lower()}.md"), "\n".join(lines))

    def status(self):
        st = lint.room_stats(self.committed())
        a = self.s["pending"]
        fr = [t["n"] for t in self.s["turns"] if t.get("friction")]
        lines = [f"Room: {self.cfg['title']}  phase={self.s['phase']}",
                 f"Turns committed: {st['turns']}  words: {st['words']} / budget {self.cfg['run_of_show']['word_budget']}"
                 f" (hard stop {self.cfg['run_of_show']['hard_stop']})",
                 f"Short turns (<=10 words): {st['short_turns']} ({int(100 * st['short_share'])}%, target >=35%)  "
                 f"guest median: {st['guest_median_words']} words (target 15-25)  contractions/100w: {st['contractions_per_100w']}",
                 f"Booked friction: {'turn T' + str(fr[0]) if fr else 'not yet'}"]
        if self.s["audit"]:
            lines.append(f"Audit: {len(self.s['audit']['flags'])} flags")
        lines.append("")
        lines.append(self.instructions())
        return "\n".join(lines)
