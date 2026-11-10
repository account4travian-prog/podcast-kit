"""Deterministic checks: every rule that code can test, so no model has to.

Two severities:
  HARD  - the reply can't be used as-is (format, banned forms, law-level rules).
          The speaker gets ONE format repair; the kit never asks a model to judge these.
  HINT  - measured facts handed to the watcher (and shown to speakers as room
          stats). The watcher decides; the kit never objects on a hint by itself.

Sources (as of producer ruling): PIPELINE.md (speakable text, turn
budgets, name-only floor offers, standing bans), PRESENTATION.md (tag
syntax and budgets, delivery cells), AVOID-LIST.md (shape hints incl.
shape 10, structural spent-shape).
"""
import re
from statistics import median

SPAN_TAGS = {"emphasis", "stress", "soft", "whisper", "fast", "slow", "forceful"}
POINT_WORDS = ("sigh", "laugh", "chuckle", "breath", "inhale", "exhale", "scoff", "gasp", "hesitat",
               "pause", "clears throat", "clear throat", "hum", "tsk", "snort", "groan", "sniff", "cough",
               "click", "swallow", "giggle")

FAST = ("fast", "quick", "rapid", "brisk", "rush", "hurried", "tumbl", "picking up", "speeding", "rattl")
SLOW = ("slow", "measured", "deliberate", "placing", "placed", "unhurried", "careful", "drawn out", "each word",
        "every word")
LOW = ("low", "quiet", "soft", "murmur", "under her breath", "under his breath", "almost to", "hushed", "gentle",
       "muted", "aside", "wry", "dry")
LOUD = ("loud", "forceful", "energetic", "bright", "emphatic", "insist", "delighted", "passionate", "impassioned",
        "pressing", "raised", "big", "animated", "excited", "booming", "sharp")

CONTRACTABLE = [
    r"\b(it|that|there|what|he|she|who|here|where) is\b", r"\bI am\b", r"\b(you|we|they) are\b",
    r"\b(is|are|was|were|do|does|did|has|have|had|could|would|should|must) not\b", r"\bcannot\b",
    r"\bwill not\b", r"\b(I|you|we|they|he|she|it) will\b", r"\blet us\b",
]
CONTRACTION = re.compile(r"\b\w+'(s|t|re|ll|d|ve|m)\b|\b(can't|won't|don't|isn't)\b", re.I)

FLOOR_OFFER = re.compile(r"\b(do|would) you (want|like|care) (in|to (come|jump|weigh) in)|passing is fine|"
                         r"want to (come|jump|weigh) in|any thoughts on that|anything to add", re.I)
NARRATE_SILENCE = re.compile(r"\b(is|seems|looks) (deciding|thinking it over|quiet|lost for words)|answer for (him|her|them)|"
                             r"on (his|her|their) behalf|(he|she)'?s? (not|isn't) (answering|saying)", re.I)
AUTH_ADVERB = re.compile(r"\b(genuinely|truly|honestly|frankly|sincerely)\b", re.I)
HERALD = re.compile(r"\b(here'?s the thing|here is the thing|here'?s what|let me (turn|start|say|put it)|"
                    r"the (key|real|important) (point|question|thing) (is|here)|what (i|we) need to (say|remember))\b", re.I)
ANTITHESIS_END = re.compile(r"(,|—|–|-)\s*not\s+(a|an|the|just|only|what|how|why|\w+)\b[^.?!]*[.?!]?$|"
                            r"\bnot\s+(a|an|the)\s+\w+[^.?!]*,\s*but\b|\bis an? \w+,\s*not\b", re.I)
TAG_RE = re.compile(r"\[([^\[\]]*)\]")
SPAN_RE = re.compile(r"</?([a-zA-Z_]+)>")


def strip_tags(t):
    t = TAG_RE.sub(" ", t)
    t = SPAN_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def words(t):
    return len(strip_tags(t).split())


def sentences(t):
    s = re.split(r"(?<=[.?!])\s+(?=[\"'A-Z\[<])", t.strip())
    return [x for x in s if strip_tags(x)]


def classify_bracket(content):
    c = content.lower().strip()
    if len(c.split()) <= 3 and any(w in c for w in POINT_WORDS):
        return "point"
    return "direction"


def cell_of(direction):
    d = direction.lower()
    pace = "fast" if any(w in d for w in FAST) else "slow" if any(w in d for w in SLOW) else None
    vol = "loud" if any(w in d for w in LOUD) else "low" if any(w in d for w in LOW) else None
    if not pace and not vol:
        return "other"
    return f"{vol or 'mid'}-{pace or 'mid'}"


def turn_shape(text, speaker_role):
    """Structural signature for the structural spent-shape test (avoid-list shape 10)."""
    ss = sentences(text)
    plain = [strip_tags(s) for s in ss]
    shape = set()
    total = sum(len(p.split()) for p in plain)
    # a short verdict that opens a LONG argued turn ("No. An average is exactly where...");
    # a short reaction opening a short turn ("Huh. Alex, what does it count?") is not this shape
    if plain and len(plain[0].split()) <= 5 and len(plain) > 1 and total >= 30:
        shape.add("verdict-first")
    if len(ss) > 1 and re.search(r"[.?!]\s*\[pause\]", text, re.I) and \
            text.lower().find("[pause]") < len(ss[0]) + 12:
        shape.add("pause-after-verdict")
    if plain and ANTITHESIS_END.search(plain[-1]):
        shape.add("antithesis-closer")
    if speaker_role == "host" and plain and plain[-1].endswith("?") and len(plain) >= 2 and \
            not plain[0].endswith("?") and len(plain[0].split()) >= 8:
        shape.add("recap-then-question")
    return sorted(shape)


CARD_EXAMPLES = {"brisk, under the breath, amused", "slow, weighing it", "quick, low, almost to herself",
                 "slower, every word placed", "louder, enjoying this", "under her breath, amused", "flat, final",
                 "quick, light, offhand", "slow, low, every word placed"}

LENGTH_CUES = {
    "close": (70, "LENGTH FOR THIS TURN: seventy words or fewer."),
    "short": (10, "LENGTH FOR THIS TURN: ten words or fewer. A reaction, a question, a one-line answer - "
                  "the room needs short turns (producer ruling: at least a third of turns are ten words or fewer)."),
    "medium": (30, "LENGTH FOR THIS TURN: thirty words or fewer - your last turn ran long; vary it."),
    "host": (40, "LENGTH FOR THIS TURN: forty words or fewer - the host keeps it short and lets the guest talk."),
}


def length_cue(role, mode, turns, speaker):
    """Deterministic per-turn length cue (turn budgets as enforcement). Lengths only, never wording."""
    if mode == "closing":
        return "close"
    if mode in ("opening", "solo"):
        return None
    mine = [t for t in turns if t["speaker"] == speaker]
    if len(turns) >= 2:
        short = sum(1 for t in turns if words(t["text"]) <= 10)
        mine_last_short = bool(mine) and words(mine[-1]["text"]) <= 10
        if short / len(turns) < 0.30 and words(turns[-1]["text"]) > 10 and not mine_last_short:
            return "short"
    if role == "host":
        return "host"
    if mine and words(mine[-1]["text"]) > 30:
        return "medium"
    return None


def check(text, speaker, role, cast_names, prior_turns, reason=None, cue=None):
    """Return dict(hard=[...], hints=[...], facts={...}) for a proposed turn.
    prior_turns: list of committed dicts {speaker, role, text}."""
    hard, hints = [], []
    raw = text.strip()
    plain = strip_tags(raw)
    w = len(plain.split())
    facts = {"words": w}

    # --- format (HARD) ---
    if not plain:
        hard.append("The turn has no spoken words.")
    if re.match(rf"^\s*(\*\*)?({'|'.join(map(re.escape, cast_names))})(\*\*)?\s*:", raw):
        hard.append("Don't start with a speaker label - just the words.")
    if re.search(r"\*|#|`|^\s*[-•]\s", raw, re.M):
        hard.append("No markdown (asterisks, #, backticks, bullet lists) - this is speech.")
    if re.search(r"\([^)]*\)", raw):
        hard.append("No parentheses - speech has no brackets except performance tags in [ ] and the allowed <span> tags.")
    if "<|" in raw:
        hard.append("Never write <|speaker|> tags - production adds those.")
    if re.search(r"\d", plain):
        hard.append("Write numbers as spoken words (\"twelve percent\", \"a hundred and ninety-six\"), not digits.")
    if re.search(r"\{|\}", raw):
        hard.append("No curly braces.")

    # --- tags (HARD on syntax/budget, HINT on use) ---
    brackets = TAG_RE.findall(raw)
    points = [b for b in brackets if classify_bracket(b) == "point"]
    dirs = [b for b in brackets if classify_bracket(b) == "direction"]
    facts.update({"point_tags": points, "directions": dirs})
    if len(points) > 2:
        hard.append(f"Too many sound tags ({len(points)}): at most two per turn ({', '.join(points)}).")
    for d in dirs:
        n = len(d.split())
        if n > 10:
            hard.append(f"Direction [{d}] is {n} words - keep directions to 2-8 words.")
    for i in range(1, len(dirs)):
        if dirs[i].strip().lower() == dirs[i - 1].strip().lower():
            hard.append(f"Direction [{dirs[i]}] repeats the one before it - drop it.")
    recent = set()
    for t in prior_turns[-2:]:
        recent |= {b.strip().lower() for b in TAG_RE.findall(t["text"]) if classify_bracket(b) == "direction"}
    for d in dirs:
        dl = d.strip().lower()
        if dl.startswith("example") or dl in CARD_EXAMPLES:
            hard.append(f"[{d}] is copied from the examples. Write your own direction for this sentence, or none.")
        elif dl in recent:
            hard.append(f"[{d}] was already used in the last two turns. Write a direction that fits THIS sentence, "
                        "or leave it plain.")
    for s in sentences(raw):
        if len([b for b in TAG_RE.findall(s) if classify_bracket(b) == "direction"]) > 1:
            hard.append(f"More than one direction in one sentence: \"{s[:60]}...\"")
            break
    stack = []
    for m in SPAN_RE.finditer(raw):
        tag, closing = m.group(1).lower(), m.group(0).startswith("</")
        if tag not in SPAN_TAGS:
            hard.append(f"<{tag}> is not an allowed span tag (allowed: {', '.join(sorted(SPAN_TAGS))}).")
            continue
        if closing:
            if not stack or stack[-1] != tag:
                hard.append(f"</{tag}> closes nothing - span tags must open and close in order.")
            else:
                stack.pop()
        else:
            stack.append(tag)
    if stack:
        hard.append(f"Unclosed span tag(s): {', '.join('<' + s + '>' for s in stack)}.")
    for m in re.finditer(r"<(\w+)>(.*?)</\1>", raw):
        if len(strip_tags(m.group(2)).split()) > 6:
            hint = f"<{m.group(1)}> wraps {len(strip_tags(m.group(2)).split())} words; span tags work on one to five."
            hints.append(hint)

    # --- law-level hosting rules (HARD) ---
    if role == "host" and FLOOR_OFFER.search(plain):
        hard.append("Floor offers are name-only: just the name as a question (\"Alex?\") - producer ruling.")
    # the short cue asks for <=10 but is enforced at <=12: in testing a weaker model answered "ten or fewer"
    # with 11-13 words, and bouncing those cost repairs without changing the conversation. The record still
    # reports the true <=10 share against the target.
    if cue in LENGTH_CUES and w > LENGTH_CUES[cue][0] + (2 if cue == "short" else 0):
        hard.append(f"This turn had to be {LENGTH_CUES[cue][0]} words or fewer; it is {w}. "
                    "Say the same thing shorter, or say less.")
    elif role == "guest" and w > 50 and not (reason and reason.strip()):
        hard.append(f"The turn is {w} words. Guest turns over fifty words need a REASON line "
                    "(a real worked example), or cut it down - most turns are ten to thirty words.")

    # --- hints for the watcher ---
    if NARRATE_SILENCE.search(plain):
        hints.append("Possible narration of someone's silence / answering for someone (a standing ban).")
    m = AUTH_ADVERB.search(plain)
    if m:
        hints.append(f"Performed-authenticity adverb candidate: \"{m.group(0)}\" (avoid-list shape 6).")
    m = HERALD.search(plain)
    if m:
        hints.append(f"Herald / announced-transition candidate: \"{m.group(0)}\" (shapes 2, 5).")
    misses = []
    for pat in CONTRACTABLE:
        misses += [x.group(0) for x in re.finditer(pat, plain, re.I)]
    contractions = len(CONTRACTION.findall(plain))
    facts.update({"uncontracted": misses, "contractions": contractions})
    if len(misses) >= 2 and contractions < len(misses):
        hints.append(f"Read-aloud: {len(misses)} uncontracted forms a speaker would usually contract "
                     f"({', '.join(sorted(set(m.lower() for m in misses)))}).")
    for s in sentences(plain):
        breaks = len(re.findall(r"[,;:—–]", s))
        if breaks > 3:
            hints.append(f"Read-aloud: a sentence with {breaks} clause breaks (each renders as a pause): \"{s[:70]}...\"")
            break
    shape = turn_shape(raw, role)
    facts["shape"] = shape
    mine = [t for t in prior_turns if t["speaker"] == speaker]
    for comp in shape:
        used = sum(1 for t in mine if comp in turn_shape(t["text"], t["role"]))
        if used >= 2:
            hints.append(f"Structural spent-shape: '{comp}' - {speaker} has already used this turn architecture "
                         f"{used} times this room (avoid-list shape 10).")
    # delivery plan
    cells = [cell_of(d) for d in dirs]
    facts["cells"] = cells
    n_sent = len(sentences(raw))
    if n_sent >= 3 and not dirs:
        hints.append("Flat plan candidate: three or more sentences and no delivery direction - does the feeling "
                     "really stay the same throughout? (tag defect: flat plan)")
    my_cells = [turn_cell(t["text"]) for t in mine[-2:]]
    this_cell = cells[0] if cells else "plain"
    if len(my_cells) == 2 and my_cells[0] == my_cells[1] == this_cell:
        if this_cell not in ("plain", "other"):
            hard.append(f"Stuck delivery: your last two turns were directed '{this_cell}' too. Real delivery moves "
                        "with the content - pick a different pace or volume, or leave this turn plain.")
        elif n_sent >= 3:
            hints.append(f"Stuck cell candidate: {speaker}'s third consecutive turn in the same delivery cell "
                         f"('{this_cell}').")
    # facts the watcher needs for the contingency check
    if prior_turns:
        prev = prior_turns[-1]
        facts["previous_speaker"] = prev["speaker"]
    return {"hard": hard, "hints": hints, "facts": facts}


def autofix_tags(text, speaker, role, prior_turns):
    """Last resort after a failed repair: remove the performance marks that break the rules.
    Safe by construction - a mark never carries content (DRAMA3-PRESENTATION), so every WORD is kept.
    Returns (fixed_text, list_of_removed_marks)."""
    removed = []
    recent = set()
    for t in prior_turns[-2:]:
        recent |= {b.strip().lower() for b in TAG_RE.findall(t["text"]) if classify_bracket(b) == "direction"}
    mine = [t for t in prior_turns if t["speaker"] == speaker]
    my_cells = [turn_cell(t["text"]) for t in mine[-2:]]
    stuck = my_cells[0] if len(my_cells) == 2 and my_cells[0] == my_cells[1] and my_cells[0] not in ("plain", "other") else None
    out, prev_dir, points = [], None, 0
    for s in sentences(text):
        seen_dir = False

        def repl(m):
            nonlocal seen_dir, prev_dir, points
            b = m.group(1)
            bl = b.strip().lower()
            if classify_bracket(b) == "point":
                points += 1
                if points > 2:
                    removed.append(m.group(0)); return ""
                return m.group(0)
            bad = (seen_dir or len(b.split()) > 10 or bl.startswith("example") or bl in CARD_EXAMPLES
                   or bl in recent or bl == prev_dir or (stuck and cell_of(b) == stuck))
            if bad:
                removed.append(m.group(0)); return ""
            seen_dir, prev_dir = True, bl
            return m.group(0)
        out.append(TAG_RE.sub(repl, s))
    fixed = " ".join(out)
    # span tags: unwrap anything not allowed or not properly paired
    for m in list(SPAN_RE.finditer(fixed)):
        if m.group(1).lower() not in SPAN_TAGS:
            removed.append(m.group(0))
    fixed = re.sub(r"</?(?!(?:" + "|".join(SPAN_TAGS) + r")>)[a-zA-Z_]+>", "", fixed)
    depth_ok = True
    stack = []
    for m in SPAN_RE.finditer(fixed):
        tag, closing = m.group(1).lower(), m.group(0).startswith("</")
        if closing:
            if not stack or stack[-1] != tag:
                depth_ok = False
            else:
                stack.pop()
        else:
            stack.append(tag)
    if stack or not depth_ok:
        removed += SPAN_RE.findall(fixed)
        fixed = SPAN_RE.sub("", fixed)
    fixed = re.sub(r"\s+", " ", fixed).strip()
    assert strip_tags(fixed).split() == strip_tags(text).split(), "autofix must never change words"
    return fixed, removed


def turn_cell(text):
    d = [b for b in TAG_RE.findall(text) if classify_bracket(b) == "direction"]
    return cell_of(d[0]) if d else "plain"


def room_stats(turns):
    """turns: committed dicts {speaker, role, text}. Facts only - used in prompts and records."""
    out = {}
    guests = [t for t in turns if t["role"] == "guest"]
    allw = [words(t["text"]) for t in turns]
    out["turns"] = len(turns)
    out["words"] = sum(allw)
    out["short_turns"] = sum(1 for x in allw if x <= 10)
    out["short_share"] = round(out["short_turns"] / len(allw), 2) if allw else 0.0
    gw = [words(t["text"]) for t in guests]
    out["guest_median_words"] = median(gw) if gw else 0
    plain = " ".join(strip_tags(t["text"]) for t in turns)
    nw = max(1, len(plain.split()))
    out["contractions_per_100w"] = round(100 * len(CONTRACTION.findall(plain)) / nw, 1)
    per = {}
    for t in turns:
        p = per.setdefault(t["speaker"], {"turns": 0, "words": [], "shapes": {}, "cells": [], "points": 0,
                                          "directions": 0})
        p["turns"] += 1
        p["words"].append(words(t["text"]))
        for s in turn_shape(t["text"], t["role"]):
            p["shapes"][s] = p["shapes"].get(s, 0) + 1
        p["cells"].append(turn_cell(t["text"]))
        br = TAG_RE.findall(t["text"])
        p["points"] += sum(1 for b in br if classify_bracket(b) == "point")
        p["directions"] += sum(1 for b in br if classify_bracket(b) == "direction")
    out["per_speaker"] = per
    return out


def room_notes_for(speaker, turns, budget):
    """Plain-language room facts shown to the next speaker. Facts and budgets, never wording."""
    st = room_stats(turns)
    notes = []
    if st["turns"]:
        notes.append(f"So far: {st['turns']} turns, {st['words']} words (budget about {budget}). "
                     f"{st['short_turns']} of {st['turns']} turns were ten words or fewer - the show wants at least a third.")
    me = st["per_speaker"].get(speaker)
    if me:
        notes.append(f"Your turns so far: {', '.join(str(x) for x in me['words'])} words.")
        spent = [k for k, v in me["shapes"].items() if v >= 2]
        if spent:
            names = {"verdict-first": "opening on a short verdict", "pause-after-verdict": "a pause right after an opening verdict",
                     "antithesis-closer": "ending on a polished 'X, not Y' line", "recap-then-question": "recapping a fact then asking a question"}
            notes.append("Already used twice or more by you - don't build this turn that way: "
                         + "; ".join(names.get(s, s) for s in spent) + ".")
        if len(me["cells"]) >= 2:
            notes.append(f"Your delivery in your last turns: {', '.join(me['cells'][-3:])}.")
    return notes
