"""Voice casting under producer ruling: the guest authors the instruction; production measures.

  cast-brief DIR  -> prompt for the guest agent (podcast-mic, baseline + range,
                     quick + weighty reference lines)
  cast-submit DIR -> parses + validates the reply, writes casting.json
  cast-design DIR -> ALPHA (in development): generate the candidates inside the
                     kit - Fish voice-design from the accepted INSTRUCTION, one
                     fast-trained model per candidate, quick + slow renders.
                     Needs FISH_API_KEY. Until it leaves alpha, verify every
                     batch by ear and with cast-check.
  cast-check DIR  -> measures candidate audio against the production gates:
       cand-N.wav         the voice-design sample for candidate N
       cand-N-quick.wav   candidate N reading QUICK_LINE under [quick, light, offhand]
       cand-N-slow.wav    candidate N reading WEIGHTY_LINE under [slow, low, every word placed]
     Gates (before the ear): bandwidth (3.4-8 kHz share within ~6 dB of the cast's
     other voices AND 95% rolloff >= ~2.5 kHz), directability (speech-only syllable
     rate differs >= 25% between quick and slow, AND level or pitch also moves).
     Pace is reported last. The ear picks among survivors.
"""
import glob
import json
import os
import re

import numpy as np

from .room import parse_fields, read, write, fsha, now
from . import remix_turns as rx
from . import prosody_report as pr

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def save_json(path, obj):
    """Atomic write: a crash can never leave a half-written record."""
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1, default=float)
    os.replace(tmp, path)
BANNED = ["lecture hall", "lecture-hall", "carries a room", "carry a room", "fills a room", "stage", "project",
          "booming", "theatrical", "never slow", "never quiet", "never soft", "throughout"]


def brief(d, name, full_name, dossier):
    os.makedirs(d, exist_ok=True)
    tpl = read(os.path.join(KIT, "prompts", "cast-voice.md"))
    p = tpl.replace("{{FULL_NAME}}", full_name).replace("{{DOSSIER}}", read(dossier).strip())
    write(os.path.join(d, "cast.prompt.md"), p)
    json.dump({"name": name, "full_name": full_name, "dossier": os.path.abspath(dossier), "dossier_sha256": fsha(dossier),
               "created": now()}, open(os.path.join(d, "casting.json"), "w"), indent=1)
    print(f"1. Start a FRESH agent. Give it the entire contents of {os.path.join(d, 'cast.prompt.md')}\n"
          f"2. Save its entire reply to {os.path.join(d, 'cast.reply.md')}\n"
          f"3. Run: python moe.py cast-submit \"{d}\"")


def submit(d):
    c = json.load(open(os.path.join(d, "casting.json")))
    reply = read(os.path.join(d, "cast.reply.md"))
    f = parse_fields(reply, "INSTRUCTION")
    probs = []
    if not f:
        probs.append("no INSTRUCTION: line")
    else:
        for k in ("QUICK_LINE", "WEIGHTY_LINE"):
            if not f.get(k):
                probs.append(f"no {k}: line")
        low = f.get("INSTRUCTION", "").lower()
        hits = [b for b in BANNED if b in low]
        if hits:
            probs.append(f"instruction uses projection / no-range words: {', '.join(hits)} (casting-brief ruling)")
        for k in ("QUICK_LINE", "WEIGHTY_LINE"):
            if re.search(r"\d", f.get(k, "")):
                probs.append(f"{k} has digits - numbers as words")
            if len(f.get(k, "").split()) > 25:
                probs.append(f"{k} is over twenty-five words")
    if probs:
        print("NOT ACCEPTED:\n- " + "\n- ".join(probs))
        print("Re-run the brief with a FRESH agent (append these problems to the prompt as 'Fix:' lines).")
        raise SystemExit(1)
    c.update({"instruction": f["INSTRUCTION"], "quick_line": f["QUICK_LINE"], "weighty_line": f["WEIGHTY_LINE"],
              "accepted": now()})
    save_json(os.path.join(d, "casting.json"), c)
    print("ACCEPTED. Next, generate the candidates. ALPHA route (inside the kit, needs FISH_API_KEY): "
          f"python moe.py cast-design \"{d}\" - it designs from INSTRUCTION, trains one model per candidate "
          "and renders the quick/slow lines. Manual route: generate 4-5 voice-design candidates externally, "
          "save each as cand-N.wav, train one model per candidate (model-cand-N.json), render QUICK_LINE with "
          "[quick, light, offhand] -> cand-N-quick.wav and WEIGHTY_LINE with [slow, low, every word placed] "
          f"-> cand-N-slow.wav. Either way, finish with: python moe.py cast-check \"{d}\"")


FISH_ROOT = os.environ.get("FISH_API_ROOT", "https://api.fish.audio")


def design(d, n=4, reference="quick"):
    """ALPHA - in development. Generate voice candidates without leaving the kit:
    voice-design from the accepted INSTRUCTION (reference_text = QUICK_LINE by
    default, or WEIGHTY_LINE with reference="weighty"; the API caps it at 150
    chars), one fast-trained private model per candidate (model-cand-N.json),
    then the quick/slow renders cast-check measures. Numbering continues after
    the highest existing candidate, so a re-run adds a fresh batch. Needs
    FISH_API_KEY in the environment (never in a file)."""
    import base64

    import requests

    c = json.load(open(os.path.join(d, "casting.json")))
    if "accepted" not in c:
        raise SystemExit("no accepted brief in casting.json - run cast-submit first")
    ref_key = "quick_line" if reference == "quick" else "weighty_line"
    ref_text = c[ref_key]
    if len(ref_text) > 150:
        raise SystemExit(f"{ref_key} is {len(ref_text)} chars; the voice-design API caps reference_text at 150. "
                         "Use --reference with the shorter line, or re-brief with a shorter line.")
    key = os.environ.get("FISH_API_KEY")
    if not key:
        raise SystemExit("FISH_API_KEY is not set. Export the key for this process (environment only, never in a file).")
    auth = {"Authorization": f"Bearer {key}"}
    existing = []
    for p in glob.glob(os.path.join(d, "cand-*.wav")):
        m = re.match(r"cand-(\d+)\.wav$", os.path.basename(p))
        if m:
            existing.append(int(m.group(1)))
    start = (max(existing) + 1) if existing else 0
    print("ALPHA FEATURE: cast-design is in alpha development. It calls the Fish voice-design and")
    print("model APIs directly - verify every batch by ear and with cast-check before trusting it.")
    r = requests.post(FISH_ROOT + "/v1/voice-design",
                      headers={**auth, "model": "voice-design-1", "Content-Type": "application/json"},
                      json={"instruction": c["instruction"], "reference_text": ref_text, "language": "en", "n": n},
                      timeout=300)
    if r.status_code != 200:
        raise SystemExit(f"voice-design failed: HTTP {r.status_code}: {r.text[:300]}")
    cands = r.json().get("candidates", [])
    if not cands:
        raise SystemExit("voice-design returned no candidates")
    done = []
    for i, cand in enumerate(cands):
        k = start + i
        wav = os.path.join(d, f"cand-{k}.wav")
        with open(wav, "wb") as fh:
            fh.write(base64.b64decode(cand["audio_base64"]))
        title = f"{c['name']} cand-{k}"
        with open(wav, "rb") as fh:
            audio = fh.read()
        mr = requests.post(FISH_ROOT + "/model", headers=auth,
                           data={"type": "tts", "title": title, "train_mode": "fast",
                                 "visibility": "private", "texts": ref_text},
                           files={"voices": (os.path.basename(wav), audio, "audio/wav")}, timeout=300)
        if mr.status_code not in (200, 201):
            raise SystemExit(f"model training failed for cand-{k}: HTTP {mr.status_code}: {mr.text[:300]} "
                             f"(cand-{k}.wav is saved; a re-run continues at the next free index)")
        payload = mr.json()
        mid = payload.get("id") or payload.get("_id")
        save_json(os.path.join(d, f"model-cand-{k}.json"),
                  {"id": mid, "title": title, "state": payload.get("state")})
        for tag, line, direction in (("quick", c["quick_line"], "[quick, light, offhand]"),
                                     ("slow", c["weighty_line"], "[slow, low, every word placed]")):
            tr = requests.post(FISH_ROOT + "/v1/tts",
                               headers={**auth, "model": "drama-3", "Content-Type": "application/json"},
                               json={"text": f"{direction} {line}", "reference_id": mid, "format": "wav",
                                     "sample_rate": 44100, "temperature": 0.8, "top_p": 0.8}, timeout=300)
            if tr.status_code != 200:
                raise SystemExit(f"tts render failed for cand-{k}-{tag}: HTTP {tr.status_code}: {tr.text[:300]}")
            with open(os.path.join(d, f"cand-{k}-{tag}.wav"), "wb") as fh:
                fh.write(tr.content)
        done.append(k)
        print(f"cand-{k}: model {mid} - quick + slow renders written")
    rec = c.get("design") or []
    rec.append({"when": now(), "tool": "cast-design (ALPHA)", "n": len(done),
                "reference": ref_key, "candidates": done})
    c["design"] = rec
    save_json(os.path.join(d, "casting.json"), c)
    print(f"\n{len(done)} candidate(s) ready: {done}. Next: python moe.py cast-check \"{d}\"")

def spectrum(x, sr=44100):
    n = 4096
    fr = np.lib.stride_tricks.sliding_window_view(x, n)[::2048]
    r = np.sqrt((fr ** 2).mean(1))
    fr = fr[r > np.percentile(r, 60)]
    S = (np.abs(np.fft.rfft(fr * np.hanning(n), axis=1)) ** 2).mean(0)
    f = np.fft.rfftfreq(n, 1 / sr)
    cs = np.cumsum(S) / S.sum()
    return float(f[np.searchsorted(cs, 0.95)]), float(10 * np.log10(S[(f >= 3400) & (f < 8000)].sum() / S.sum()))


def delivery(path):
    x = rx.load(path)
    x16 = np.interp(np.arange(0, len(x), 44100 / 16000), np.arange(len(x)), x).astype(np.float32)
    db = pr.frame_rms_db(x16)
    segs = pr.segments(db)
    rates = [pr.syllable_rate(x16, a, b) for a, b in segs]
    rates = [r for r in rates if r == r]
    speech = sum(b - a for a, b in segs)
    f0 = pr.yin_f0(x16)
    v = f0[~np.isnan(f0)]
    return {"syl_per_s": float(np.median(rates)) if rates else None, "speech_s": round(speech, 2),
            "dur_s": round(len(x) / 44100, 2), "level_db": round(rx.active_db(x), 1),
            "f0_med": round(float(np.median(v)), 1) if len(v) else None}


def check(d, compare=None):
    c = json.load(open(os.path.join(d, "casting.json")))
    ref_share = None
    if compare:
        ref_roll, ref_share = spectrum(rx.load(compare))
        print(f"comparison voice: rolloff {ref_roll:.0f} Hz, 3.4-8 kHz share {ref_share:.1f} dB")
    rows = []
    for p in sorted(glob.glob(os.path.join(d, "cand-*.wav"))):
        m = re.match(r"cand-(\d+)\.wav$", os.path.basename(p))
        if not m:
            continue
        n = m.group(1)
        roll, share = spectrum(rx.load(p))
        row = {"cand": n, "rolloff_hz": round(roll), "presence_db": round(share, 1)}
        row["bandwidth_pass"] = roll >= 2500 and (ref_share is None or share >= ref_share - 6)
        q, s = os.path.join(d, f"cand-{n}-quick.wav"), os.path.join(d, f"cand-{n}-slow.wav")
        if os.path.exists(q) and os.path.exists(s):
            dq, ds = delivery(q), delivery(s)
            ratio = (dq["syl_per_s"] / ds["syl_per_s"]) if dq["syl_per_s"] and ds["syl_per_s"] else None
            level_move = abs(dq["level_db"] - ds["level_db"])
            pitch_move = abs(12 * np.log2(dq["f0_med"] / ds["f0_med"])) if dq["f0_med"] and ds["f0_med"] else 0
            row.update({"quick_syl_s": round(dq["syl_per_s"] or 0, 2), "slow_syl_s": round(ds["syl_per_s"] or 0, 2),
                        "rate_ratio": round(ratio, 2) if ratio else None, "level_move_db": round(level_move, 1),
                        "pitch_move_st": round(float(pitch_move), 1),
                        "slow_pause_share": round(1 - ds["speech_s"] / ds["dur_s"], 2)})
            row["directability_pass"] = bool(ratio and ratio >= 1.25 and (level_move >= 2 or pitch_move >= 1))
        else:
            row["directability_pass"] = None
        row["survives"] = bool(row["bandwidth_pass"] and row["directability_pass"])
        rows.append(row)
    if not rows:
        raise SystemExit(f"no cand-N.wav files in {d}")
    c["gates"] = {"checked": now(), "compare": compare, "candidates": rows}
    save_json(os.path.join(d, "casting.json"), c)
    print(f"{'cand':>4} {'rolloff':>8} {'pres dB':>8} {'band':>5} {'rate q/s':>9} {'lvl mv':>7} {'f0 mv':>6} {'direct':>7} {'SURVIVES':>9}")
    for r in rows:
        print(f"{r['cand']:>4} {r['rolloff_hz']:>8} {r['presence_db']:>8} {str(r['bandwidth_pass']):>5} "
              f"{str(r.get('rate_ratio')):>9} {str(r.get('level_move_db')):>7} {str(r.get('pitch_move_st')):>6} "
              f"{str(r['directability_pass']):>7} {str(r['survives']):>9}")
    surv = [r["cand"] for r in rows if r["survives"]]
    print(f"\nSurvivors: {surv or 'NONE - generate more candidates; do not cast a voice that failed a gate'}")
    print("Pace is reported last and is not a gate. The producer's ear picks among survivors; record the pick, "
          "the model id and these numbers in the guest's dossier.")
