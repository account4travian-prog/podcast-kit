"""Render -> per-turn files -> mix -> prosody report, for a closed room.

  render   Drama 3 via HTTP (FISH_API_KEY). SCENE mode = consecutive turns in one
           request so the engine hears what it's answering (producer ruling). TURN mode = one
           request per turn (fallback). Manifest + hashes for every take.
  split    Scene takes are cut back into per-turn files (turns/T##.wav) by a
           dynamic-programming fit of the known turn order + word counts to the
           take's silences, with a pitch cue at speaker changes.
  mix      One gain per SPEAKER (never per turn), trimmed render heads/tails,
           gaps (scene: the engine's own measured gaps; turn: rule-based), two-pass
           LINEAR master to -16 LUFS. Writes mix/room-mix.mp3 and an unmastered
           mix/room-mix.wav for episode assembly.
  report   tools prosody report on turns/T##.wav -> prosody.json (spreads per speaker).
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

from . import lint
from . import remix_turns as rx
from . import render_drama3 as rd
from . import prosody_report as pr

SR = 44100


def _dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                capture_output=True, text=True).stdout)


def _turns(room, script):
    path = os.path.join(room.path, "TRANSCRIPT-RETAGGED.md" if script == "retagged" else "TRANSCRIPT.md")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found" + (" - run `moe.py retag` first" if script == "retagged" else ""))
    text = open(path, encoding="utf-8").read()
    out = []
    for n, s, b in re.findall(r"\*\*T(\d+) · ([^:*]+):\*\* (.+?)(?=\n\*\*T\d+ |\Z)", text, re.S):
        out.append((int(n), s.strip(), " ".join(b.split())))
    return sorted(out)


# ------------------------------------------------------------------ render
def render(room, mode="scene", temperature=0.8, top_p=0.8, takes=1, max_chars=1400, script="committed",
           dry_run=False, tts_cmd=None):
    if room.s["phase"] not in ("closed",):
        raise SystemExit(f"room phase is '{room.s['phase']}' - render only a closed (audited) room.")
    voices = {c["name"]: c.get("voice") for c in room.cfg["cast"]}
    missing = [n for n, v in voices.items() if not v]
    if missing:
        raise SystemExit(f"no voice model id in room.json for: {', '.join(missing)}")
    turns = _turns(room, script)
    units = rd.plan_scenes(turns, room.host, max_chars) if mode == "scene" else [[t] for t in turns]
    outdir = os.path.join(room.path, f"render-{mode}")
    os.makedirs(outdir, exist_ok=True)
    key = os.environ.get("FISH_API_KEY")
    if not dry_run and not key and not tts_cmd:
        raise SystemExit("FISH_API_KEY is not set (and no --tts-cmd given). Export the key for this process "
                         "only; never write it into a file.")
    if tts_cmd and mode == "scene":
        raise SystemExit("--tts-cmd supports TURN mode only (single voice per call). Scene mode needs the HTTP "
                         "backend (FISH_API_KEY) or a connector that accepts several reference ids.")
    order = [c["name"] for c in room.cfg["cast"]]
    manifest = {"mode": mode, "script": script, "temperature": temperature, "top_p": top_p, "units": [],
                "room_state_sha256": hashlib.sha256(open(room.state_file, "rb").read()).hexdigest()}
    total = 0
    for u, unit in enumerate(units, 1):
        spk = [s for s in order if any(t[1] == s for t in unit)]
        if mode == "scene":
            text, ref = rd.build_text(unit, spk), [voices[s] for s in spk]
        else:
            text, ref = unit[0][2], voices[unit[0][1]]
        body = {"text": text, "reference_id": ref, "format": "wav", "sample_rate": SR, "temperature": temperature,
                "top_p": top_p, "latency": "normal", "normalize": True}
        nb = len(text.encode("utf-8"))
        total += nb * takes
        entry = {"unit": u, "turns": [t[0] for t in unit], "speakers": spk, "bytes": nb, "takes": [],
                 "chosen": 1, "request": body}
        print(f"U{u:02d}: T{unit[0][0]}-T{unit[-1][0]} ({len(unit)} turns, {nb} bytes)")
        for k in range(1, takes + 1):
            p = os.path.join(outdir, f"U{u:02d}-take{k}.wav")
            if not dry_run and not os.path.exists(p):
                if tts_cmd:
                    tmp = p + ".in.txt"
                    open(tmp, "w", encoding="utf-8").write(text)
                    cmd = tts_cmd.format(text_file=tmp, out=p, voice=ref)
                    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                    os.remove(tmp)
                    if r.returncode != 0 or not os.path.exists(p):
                        raise SystemExit(f"tts-cmd failed for U{u}: {r.stderr[-400:]}")
                else:
                    rd.post(body, key, p)
            if os.path.exists(p):
                entry["takes"].append({"file": os.path.basename(p), "dur_s": round(_dur(p), 2),
                                       "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()})
                print(f"   take{k}: {entry['takes'][-1]['dur_s']}s")
        manifest["units"].append(entry)
    manifest["billed_bytes"] = total
    json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=1)
    print(f"{total} bytes -> ~${total / 1e6 * 10:.3f} at the $10/M promo (to 2026-11-30), ~${total / 1e6 * 50:.3f} at $50/M list")
    if dry_run:
        print(f"dry run - manifest written to {outdir}, nothing rendered")
    else:
        print(f"Next: pick takes by ear if you rendered more than one (edit \"chosen\" in {outdir}/manifest.json), "
              f"then `moe.py split {room.path} --mode {mode}`")


# ------------------------------------------------------------------ probe
PROBE_LINE = "[quick, light] Right, so that's the number. [slower, weighing it] Now what does it actually measure?"


def probe(voices, out_dir):
    """Cheap preflight (a few hundred bytes): every voice renders a directed line in Drama 3, and the first
    two voices render a two-speaker scene. Checks HTTP status, that audio came back, and its length."""
    import requests
    key = os.environ.get("FISH_API_KEY")
    if not key:
        raise SystemExit("FISH_API_KEY is not set for this command.")
    os.makedirs(out_dir, exist_ok=True)
    rows, ok_all, total = [], True, 0
    jobs = [(name, {"text": PROBE_LINE, "reference_id": vid}) for name, vid in voices.items()]
    names = list(voices)
    if len(names) >= 2:
        jobs.append((f"scene-{names[0]}-{names[1]}",
                     {"text": f"<|speaker:0|>[curious] So what does it measure?<|speaker:1|>[dry, quick] Less than the "
                              "headline says.<|speaker:0|>Huh. Go on.",
                      "reference_id": [voices[names[0]], voices[names[1]]]}))
    for label, body in jobs:
        body.update({"format": "wav", "sample_rate": SR, "temperature": 0.8, "top_p": 0.8, "normalize": True})
        total += len(body["text"].encode("utf-8"))
        p = os.path.join(out_dir, f"probe-{label}.wav")
        try:
            r = requests.post(rd.API, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                               "model": "drama-3"}, data=json.dumps(body), timeout=180)
        except Exception as e:  # network / DNS
            rows.append((label, f"FAIL request error: {e}")); ok_all = False
            continue
        hdr = {k: v for k, v in r.headers.items() if k.lower() not in ("set-cookie",)}
        json.dump({"status": r.status_code, "headers": hdr}, open(p + ".headers.json", "w"), indent=1)
        if r.status_code != 200 or len(r.content) < 2000:
            rows.append((label, f"FAIL HTTP {r.status_code}: {r.text[:200]}")); ok_all = False
            continue
        open(p, "wb").write(r.content)
        try:
            d = _dur(p)
            lv = rx.active_db(rx.load(p))
        except Exception as e:
            rows.append((label, f"FAIL audio unreadable: {e}")); ok_all = False
            continue
        good = 1.5 <= d <= 20 and lv > -45
        ok_all &= good
        rows.append((label, f"{'OK' if good else 'FAIL'} {d:.1f}s, active level {lv:.1f} dBFS -> {p}"))
    for label, res in rows:
        print(f"{label:<28} {res}")
    print(f"billed about {total} bytes (~${total / 1e6 * 50:.4f} even at list price)")
    print("Response headers are saved next to each file - check them for the model if Fish reports it.")
    print("PROBE OK - the key, the drama-3 model and every voice answered." if ok_all else
          "PROBE FAILED - fix the failing line before rendering a room. Report the exact message if you can't.")
    return ok_all


# ------------------------------------------------------------------ split
def _silences(x, hop=441, min_len=0.10):
    n = 1 + (len(x) - 1024) // hop
    fr = np.lib.stride_tricks.sliding_window_view(x, 1024)[::hop][:n]
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    sil = db < np.percentile(db, 95) - 35
    runs, i = [], 0
    while i < len(sil):
        if sil[i]:
            j = i
            while j < len(sil) and sil[j]:
                j += 1
            if (j - i) * hop / SR >= min_len:
                runs.append(((i * hop) / SR, (j * hop) / SR))
            i = j
        else:
            i += 1
    return runs, db, hop


def _f0_track(x):
    x16 = x[::int(SR / 16000)] if SR % 16000 == 0 else np.interp(np.arange(0, len(x), SR / 16000), np.arange(len(x)), x)
    return pr.yin_f0(x16.astype(np.float32)), 0.010


def _dp(K, C, cost):
    """Choose K-1 strictly increasing candidates minimising the summed cost."""
    INF = 1e18
    D = np.full((K - 1, C), INF)
    P = np.zeros((K - 1, C), int)
    for j in range(C):
        D[0, j] = cost(0, j)
    for k in range(1, K - 1):
        best, bi = INF, -1
        for j in range(C):
            if j - 1 >= 0 and D[k - 1, j - 1] < best:
                best, bi = D[k - 1, j - 1], j - 1
            if bi >= 0:
                D[k, j] = best + cost(k, j)
                P[k, j] = bi
    j = int(np.argmin(D[K - 2]))
    picks = [j]
    for k in range(K - 2, 0, -1):
        j = int(P[k, j])
        picks.append(j)
    return picks[::-1]


def split_take(wav, unit_turns, speakers):
    """unit_turns: [(n, speaker, text)]. Returns list of (start_s, end_s) per turn.

    Boundaries must sit in real silences. Among those, a DP picks the set that best fits
    (a) where each turn should end given its word count and its speaker's measured pace,
    (b) a speaker-identity cue: pitch on each side of the gap should match the speakers
        known to be before and after it. Pace and per-speaker pitch are re-estimated from
        the previous pass (3 passes). Works with any cast; the pitch cue simply carries no
        weight when two voices share a pitch range.
    """
    x = rx.load(wav)
    K = len(unit_turns)
    dur = len(x) / SR
    if K == 1:
        return [(0.0, dur)], {"method": "single"}
    hop = 441
    n = 1 + (len(x) - 1024) // hop
    fr = np.lib.stride_tricks.sliding_window_view(x, 1024)[::hop][:n]
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    sil = db < np.percentile(db, 95) - 45      # true silence only: quiet speech is not a gap
    runs, i = [], 0
    while i < len(sil):
        if sil[i]:
            j = i
            while j < len(sil) and sil[j]:
                j += 1
            if (j - i) * hop / SR >= 0.08:
                runs.append((i * hop / SR, j * hop / SR))
            i = j
        else:
            i += 1
    cands = [(a, b) for a, b in runs if 0.5 < (a + b) / 2 < dur - 0.5]
    if len(cands) < K - 1:
        raise SystemExit(f"{os.path.basename(wav)}: only {len(cands)} silences for {K - 1} boundaries - "
                         "render this unit in turn mode, or check the take")
    C = len(cands)
    mids = np.array([(a + b) / 2 for a, b in cands])
    f0, f0hop = _f0_track(x)
    logf = np.log2(f0)                       # nan where unvoiced
    w = np.array([max(1, lint.words(t[2])) for t in unit_turns], float)
    spk = [t[1] for t in unit_turns]
    names = sorted(set(spk))

    def f0_med(t0, t1):
        seg = f0[max(0, int(t0 / f0hop)):max(0, int(t1 / f0hop))]
        seg = seg[~np.isnan(seg)]
        return float(np.median(seg)) if len(seg) >= 15 else None

    # seed each speaker's pitch from the edges of the take (first turn's start, last turn's end)
    pitch = {}
    if f0_med(0, 3):
        pitch[spk[0]] = f0_med(0, 3)
    if f0_med(dur - 3, dur) and spk[-1] not in pitch:
        pitch[spk[-1]] = f0_med(dur - 3, dur)
    rate = {s: 1.0 for s in names}
    picks, exp_t = None, None
    for it in range(4):
        # frame votes: +1 for the nearer speaker when the margin is over one semitone
        distinct = len(pitch) == len(names) and len(names) > 1 and \
            min(abs(12 * np.log2(pitch[a] / pitch[b])) for a in names for b in names if a < b) >= 2.0
        mism = {}
        if distinct:
            dist = {s: np.abs(12 * (logf - np.log2(pitch[s]))) for s in names}
            for s in names:
                other = np.min([dist[o] for o in names if o != s], axis=0)
                bad = np.nan_to_num((dist[s] - other) > 1.0).astype(float)   # frame clearly belongs to someone else
                mism[s] = np.concatenate([[0.0], np.cumsum(bad)])
        d = np.array([w[k] * rate[spk[k]] for k in range(K)])
        exp_t = np.cumsum(d)[:-1] / d.sum() * dur
        sig = max(2.0, 0.06 * dur) if distinct else max(1.2, 0.04 * dur)

        def seg_cost(k, t0, t1):
            if not distinct:
                return 0.0
            i0, i1 = int(t0 / f0hop), int(t1 / f0hop)
            arr = mism[spk[k]]
            i0, i1 = min(i0, len(arr) - 1), min(i1, len(arr) - 1)
            return 0.05 * (arr[i1] - arr[i0])       # 0.05 per mislabeled 10 ms voiced frame = 5 per second

        def bcost(k, j):
            return ((mids[j] - exp_t[k]) / sig) ** 2 - 0.5 * min(cands[j][1] - cands[j][0], 1.0)

        INF = 1e18
        D = np.full((K - 1, C), INF)
        P = np.full((K - 1, C), -1, int)
        for j in range(C):
            D[0, j] = seg_cost(0, 0.0, cands[j][0]) + bcost(0, j)
        for k in range(1, K - 1):
            for j in range(k, C):
                best, bi = INF, -1
                for i in range(k - 1, j):
                    if D[k - 1, i] >= INF:
                        continue
                    v = D[k - 1, i] + seg_cost(k, cands[i][1], cands[j][0])
                    if v < best:
                        best, bi = v, i
                if bi >= 0:
                    D[k, j] = best + bcost(k, j)
                    P[k, j] = bi
        final = [D[K - 2, j] + seg_cost(K - 1, cands[j][1], dur) if D[K - 2, j] < INF else INF for j in range(C)]
        j = int(np.argmin(final))
        picks = [j]
        for k in range(K - 2, 0, -1):
            j = int(P[k, j])
            picks.append(j)
        picks = picks[::-1]
        bounds = [cands[j] for j in picks]
        segs, start = [], 0.0
        for a, b in bounds:
            segs.append((start, a))
            start = b
        segs.append((start, dur))
        # re-estimate pace and pitch per speaker from this pass
        tot, pv = {}, {}
        for (s0, s1), k in zip(segs, range(K)):
            tot.setdefault(spk[k], [0.0, 0.0])
            tot[spk[k]][0] += s1 - s0
            tot[spk[k]][1] += w[k]
            v = f0_med(s0, s1)
            if v:
                pv.setdefault(spk[k], []).append(v)
        mean_rate = sum(v[0] for v in tot.values()) / sum(v[1] for v in tot.values())
        rate = {s: (v[0] / v[1]) / mean_rate for s, v in tot.items()}
        pitch = {s: float(np.median(v)) for s, v in pv.items()}
    err = [abs(mids[j] - e) for j, e in zip(picks, exp_t)]
    info = {"method": "segment-dp", "pitch_cue": bool(distinct),
            "boundaries": [[round(a, 3), round(b, 3)] for a, b in bounds],
            "expected_s": [round(float(e), 2) for e in exp_t], "speaker_f0": {k: round(v, 1) for k, v in pitch.items()},
            "max_frac_error": round(float(max(err) / dur), 3)}
    return segs, info


ASR_API = os.environ.get("FISH_ASR_URL", "https://api.fish.audio/v1/asr")


def _norm_tokens(text):
    t = lint.strip_tags(text).lower().replace("’", "'")
    return [w for w in re.sub(r"[^a-z0-9' ]+", " ", t).split() if w]


def asr_words(wav, key):
    """Fish speech-to-text with timestamps -> [(token, start_s, end_s)]. Segments longer than a word get their
    span shared out across their words in proportion to length."""
    import requests
    with open(wav, "rb") as fh:
        r = requests.post(ASR_API, headers={"Authorization": f"Bearer {key}", "model": "transcribe-1-pro"},
                          files={"audio": (os.path.basename(wav), fh, "audio/wav")},
                          data={"ignore_timestamps": "false", "language": "en"}, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"ASR HTTP {r.status_code}: {r.text[:200]}")
    out = []
    for seg in r.json().get("segments", []):
        toks = _norm_tokens(seg.get("text", ""))
        if not toks:
            continue
        s, e = float(seg["start"]), float(seg["end"])
        if e > 1000 and s > 100:  # milliseconds, defensively
            s, e = s / 1000, e / 1000
        lens = np.array([len(t) + 1 for t in toks], float)
        edges = s + (e - s) * np.concatenate([[0], np.cumsum(lens) / lens.sum()])
        out += [(t, float(edges[i]), float(edges[i + 1])) for i, t in enumerate(toks)]
    return out


def split_take_asr(wav, unit_turns, key):
    """Cut where the words say the turns change: align the recognised words to the known turn text,
    then cut in the longest silence between the last word of turn k and the first word of turn k+1."""
    import difflib
    x = rx.load(wav)
    dur = len(x) / SR
    K = len(unit_turns)
    if K == 1:
        return [(0.0, dur)], {"method": "single"}
    hyp = asr_words(wav, key)
    ref, owner = [], []
    for k, t in enumerate(unit_turns):
        toks = _norm_tokens(t[2])
        ref += toks
        owner += [k] * len(toks)
    sm = difflib.SequenceMatcher(None, ref, [h[0] for h in hyp], autojunk=False)
    m = {}
    for a, b, size in sm.get_matching_blocks():
        for i in range(size):
            m[a + i] = b + i
    matched = len(m) / max(1, len(ref))
    hop = 441
    n = 1 + (len(x) - 1024) // hop
    fr = np.lib.stride_tricks.sliding_window_view(x, 1024)[::hop][:n]
    db = 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9)
    sil = db < np.percentile(db, 95) - 45
    bounds, info_b = [], []
    for k in range(1, K):
        prev_idx = [i for i in range(len(ref)) if owner[i] == k - 1 and i in m]
        next_idx = [i for i in range(len(ref)) if owner[i] == k and i in m]
        if not prev_idx or not next_idx:
            raise RuntimeError(f"turn {unit_turns[k][0]} not found in the transcription")
        e_prev = hyp[m[max(prev_idx)]][2]
        s_next = hyp[m[min(next_idx)]][1]
        lo, hi = min(e_prev, s_next), max(e_prev, s_next)
        i0, i1 = max(0, int((lo - 0.25) * SR / hop)), min(len(sil), int((hi + 0.25) * SR / hop) + 1)
        best, j = None, i0
        while j < i1:  # longest silent run in the window
            if sil[j]:
                q = j
                while q < i1 and sil[q]:
                    q += 1
                if best is None or q - j > best[1] - best[0]:
                    best = (j, q)
                j = q
            else:
                j += 1
        if best:
            a, b = best[0] * hop / SR, best[1] * hop / SR
        else:
            a = b = (lo + hi) / 2
        bounds.append((a, b))
        info_b.append([round(a, 3), round(b, 3)])
    segs, start = [], 0.0
    for a, b in bounds:
        segs.append((start, a))
        start = b
    segs.append((start, dur))
    return segs, {"method": "asr", "matched_words": round(matched, 3), "boundaries": info_b, "max_frac_error": 0.0}


def split(room, mode="scene"):
    outdir = os.path.join(room.path, f"render-{mode}")
    man = json.load(open(os.path.join(outdir, "manifest.json")))
    script = man.get("script", "committed")
    turns = {t[0]: t for t in _turns(room, script)}
    tdir = os.path.join(room.path, "turns")
    if os.path.isdir(tdir):
        shutil.rmtree(tdir)
    os.makedirs(tdir)
    report = {"mode": mode, "units": []}
    warn = []
    for e in man["units"]:
        take = [t for t in e["takes"] if t["file"] == f"U{e['unit']:02d}-take{e.get('chosen', 1)}.wav"]
        if not take:
            raise SystemExit(f"U{e['unit']}: chosen take {e.get('chosen', 1)} not rendered")
        wav = os.path.join(outdir, take[0]["file"])
        ut = [turns[n] for n in e["turns"]]
        key = os.environ.get("FISH_API_KEY")
        segs, info = None, None
        if key and len(ut) > 1:
            try:
                segs, info = split_take_asr(wav, ut, key)
                if info["matched_words"] < 0.6:
                    warn.append(f"U{e['unit']}: only {int(100 * info['matched_words'])}% of words matched the "
                                "transcription - used the acoustic split instead")
                    segs = None
            except Exception as ex:  # ASR unavailable -> acoustic fallback, said out loud
                warn.append(f"U{e['unit']}: word alignment failed ({ex}); used the acoustic split")
                segs = None
        if segs is None:
            segs, info = split_take(wav, ut, e["speakers"])
            if key is None and len(ut) > 1:
                info["note"] = "acoustic split (no FISH_API_KEY for word alignment)"
        x = rx.load(wav)
        gaps_after = []
        for i, ((a, b), t) in enumerate(zip(segs, ut)):
            seg = x[int(a * SR):int(b * SR)]
            out = os.path.join(tdir, f"T{t[0]:02d}.wav")
            _write_wav(out, seg)
            if i + 1 < len(segs):
                gaps_after.append(round(segs[i + 1][0] - b, 3))
        info["gaps_after"] = gaps_after
        info["unit"] = e["unit"]
        if info.get("max_frac_error", 0) > 0.12:
            warn.append(f"U{e['unit']}: split fit is loose (max error {info['max_frac_error']}) - listen to "
                        f"turns T{e['turns'][0]}-T{e['turns'][-1]} boundaries")
        report["units"].append(info)
    json.dump(report, open(os.path.join(room.path, "turns", "split.json"), "w"), indent=1, default=float)
    print(f"wrote {len(turns)} turn files to {tdir}")
    for w in warn:
        print("WARNING:", w)
    print(f"Next: `moe.py mix {room.path}`")


def _write_wav(path, x):
    raw = path + ".f32"
    x.astype(np.float32).tofile(raw)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", raw,
                    "-c:a", "pcm_s16le", path], check=True)
    os.remove(raw)


# ------------------------------------------------------------------ mix
def rule_gap(prev, cur, prev_topic, cur_topic):
    """Turn-mode gaps (seconds of silence before `cur`). Quick replies short, topic shifts longer."""
    pw = lint.strip_tags(prev["text"])
    cw = lint.words(cur["text"])
    if cur["role"] == "host" and cur_topic and prev_topic and cur_topic != prev_topic:
        return 0.45
    if cw <= 5:
        return 0.12
    if pw.rstrip().endswith("?"):
        return 0.22
    return 0.30


def mix(room, target=-20.0):
    tdir = os.path.join(room.path, "turns")
    files = sorted(glob.glob(os.path.join(tdir, "T*.wav")))
    if not files:
        raise SystemExit("no turns/T##.wav - run `moe.py split` first")
    split_info = {}
    sp = os.path.join(tdir, "split.json")
    scene = os.path.exists(sp)
    if scene:
        for u in json.load(open(sp))["units"]:
            split_info[u["unit"]] = u
    turns = room.s["turns"]
    byn = {t["n"]: t for t in turns}
    xs = {}
    for f in files:
        n = int(re.search(r"T(\d+)", os.path.basename(f)).group(1))
        xs[n] = rx.trim(rx.load(f))
    gain = {}
    for s in {t["speaker"] for t in turns}:
        lv = [rx.active_db(xs[t["n"]]) for t in turns if t["speaker"] == s and t["n"] in xs]
        gain[s] = target - float(np.median(lv))
    # gaps
    gaps = {}
    if scene:
        man = json.load(open(os.path.join(room.path, "render-scene", "manifest.json")))
        for e in man["units"]:
            g = split_info.get(e["unit"], {}).get("gaps_after", [])
            for i, n in enumerate(e["turns"][1:]):
                gaps[n] = float(np.clip(g[i] if i < len(g) else 0.25, 0.08, 0.8))
            if e["unit"] > 1:
                prev_n = e["turns"][0] - 1
                gaps[e["turns"][0]] = rule_gap(byn[prev_n], byn[e["turns"][0]], byn[prev_n].get("topic"),
                                               byn[e["turns"][0]].get("topic"))
    parts, log = [], []
    last_topic = None
    for i, t in enumerate(turns):
        if t["n"] not in xs:
            raise SystemExit(f"missing turns/T{t['n']:02d}.wav")
        if i:
            g = gaps.get(t["n"]) if scene else rule_gap(turns[i - 1], t, last_topic, t.get("topic"))
            if g is None:
                g = 0.3
            parts.append(np.zeros(int(g * SR), np.float32))
            log.append({"before": t["n"], "gap_s": round(g, 3)})
        if t["role"] == "host" and t.get("topic"):
            last_topic = t.get("topic")
        parts.append(xs[t["n"]] * 10 ** (gain[t["speaker"]] / 20))
    y = np.concatenate(parts)
    mdir = os.path.join(room.path, "mix")
    os.makedirs(mdir, exist_ok=True)
    _write_wav(os.path.join(mdir, "room-mix.wav"), y)
    raw = os.path.join(mdir, "mix.f32")
    y.astype(np.float32).tofile(raw)
    out = os.path.join(mdir, "room-mix.mp3")
    rx.master(raw, out)
    os.remove(raw)
    info = {"speaker_gain_db": {k: round(v, 2) for k, v in gain.items()}, "gaps": log, "duration_s": round(len(y) / SR, 2),
            "mp3": out, "sha256": hashlib.sha256(open(out, "rb").read()).hexdigest(), "loudness": loudness(out)}
    json.dump(info, open(os.path.join(mdir, "mix.json"), "w"), indent=1, default=float)
    print(json.dumps({k: info[k] for k in ("speaker_gain_db", "duration_s", "loudness")}, indent=1))
    print(f"wrote {out}\nNext: `moe.py report {room.path}`")


def loudness(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    g = lambda k: (re.findall(rf"^\s+{k}:\s+(-?[\d.]+)", r, re.M) or [None])[-1]
    return {"I_LUFS": float(g("I")), "LRA_LU": float(g("LRA")), "peak_dBFS": float(g("Peak"))}


# ------------------------------------------------------------------ report
def report(room):
    out = os.path.join(room.path, "prosody.json")
    cmd = [sys.executable, pr.__file__, room.path, "--glob", "turns/T*.wav", "--json", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout[-6000:])
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])
    print(f"Next: `moe.py record {room.path}` and deliver.")


# ------------------------------------------------------------------ episode
def episode(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(cfg_path))
    out_dir = os.path.join(base, cfg.get("out_dir", "episode-build"))
    os.makedirs(out_dir, exist_ok=True)
    parts, chapters, t = [], [], 0.0
    gap = float(cfg.get("gap_s", 0.8))
    sting = cfg.get("sting")
    sting_x = rx.load(os.path.join(base, sting)) if sting else None
    for i, seg in enumerate(cfg["segments"]):
        if "room" in seg:
            p = os.path.join(base, seg["room"], "mix", "room-mix.wav")
        else:
            p = os.path.join(base, seg["audio"])
        if not os.path.exists(p):
            raise SystemExit(f"segment {i + 1} ({seg.get('title')}): {p} not found - mix that room first")
        x = rx.load(p)
        if i:
            if sting_x is not None and seg.get("sting", True):
                parts.append(np.zeros(int(0.3 * SR), np.float32)); t += 0.3
                parts.append(sting_x); t += len(sting_x) / SR
            parts.append(np.zeros(int(gap * SR), np.float32)); t += gap
        chapters.append((t, seg.get("title", f"Segment {i + 1}")))
        parts.append(x)
        t += len(x) / SR
    y = np.concatenate(parts)
    raw = os.path.join(out_dir, "ep.f32")
    y.astype(np.float32).tofile(raw)
    out = os.path.join(out_dir, cfg.get("file", "episode.mp3"))
    rx.master(raw, out)
    os.remove(raw)
    mins = len(y) / SR / 60
    band = cfg.get("band_minutes", [55, 65])
    ch = "\n".join(f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f} {title}" for s, title in chapters)
    open(os.path.join(out_dir, "chapters.txt"), "w", encoding="utf-8").write(ch + "\n")
    info = {"file": out, "minutes": round(mins, 2), "band": band, "in_band": band[0] <= mins <= band[1],
            "loudness": loudness(out), "sha256": hashlib.sha256(open(out, "rb").read()).hexdigest(),
            "chapters": [{"t": round(s, 2), "title": tt} for s, tt in chapters]}
    json.dump(info, open(os.path.join(out_dir, "episode.json"), "w"), indent=1, default=float)
    print(json.dumps(info, indent=1))
    if not info["in_band"]:
        print(f"WARNING: {mins:.1f} min is outside the {band[0]}-{band[1]} minute band - report this, don't hide it.")
