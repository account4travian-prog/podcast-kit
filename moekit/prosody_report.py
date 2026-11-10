"""Prosody report for a rendered room: does delivery actually vary?

Measures, per turn and per phrase (speech between pauses >= 250 ms):
  - pace: words/min for the turn (from the transcript), and a transcript-free
    syllable-rate estimate per phrase (energy-peak nuclei, de Jong & Wempe style)
  - pitch: median F0 and F0 range (10th-90th pct, in semitones) via YIN
  - level: active-speech RMS in dBFS
  - pauses: count and median length of internal pauses

Then reports the SPREAD, which is what the delivery-variation ruling is about: a room whose turns
cluster in one narrow band fails the variation principle even if every line
passes the deletion test.

Usage:
  python tools/prosody_report.py SAMPLE-RUN-3-2026-11-08            (uses turns/T*.mp3 + TRANSCRIPT.md)
  python tools/prosody_report.py RUN_DIR --json out.json
Requires: ffmpeg on PATH, numpy, scipy.
"""
import argparse, glob, json, os, re, subprocess, sys
import numpy as np
from scipy.signal import find_peaks, butter, sosfiltfilt

SR = 16000
FRAME = 0.010  # 10 ms hop


def load(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def frame_rms_db(x, hop=int(SR * FRAME), win=int(SR * 0.025)):
    n = 1 + max(0, (len(x) - win) // hop)
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx]
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def yin_f0(x, fmin=65, fmax=450, hop=int(SR * FRAME), win=int(SR * 0.040), thresh=0.15):
    """Plain YIN. Returns f0 per hop (np.nan where unvoiced)."""
    tau_min, tau_max = int(SR / fmax), int(SR / fmin)
    n = 1 + max(0, (len(x) - win - tau_max) // hop)
    f0 = np.full(n, np.nan)
    for i in range(n):
        s = i * hop
        fr = x[s:s + win + tau_max]
        if np.sqrt(np.mean(fr[:win] ** 2)) < 0.01:
            continue
        d = np.array([np.sum((fr[:win] - fr[t:t + win]) ** 2) for t in range(tau_max + 1)])
        cmnd = np.ones_like(d)
        cs = np.cumsum(d[1:])
        cmnd[1:] = d[1:] * np.arange(1, len(d)) / (cs + 1e-12)
        cand = np.where(cmnd[tau_min:] < thresh)[0]
        if len(cand) == 0:
            continue
        t = cand[0] + tau_min
        while t + 1 < len(cmnd) and cmnd[t + 1] < cmnd[t]:
            t += 1
        if 0 < t < len(cmnd) - 1:  # parabolic interpolation
            a, b, c = cmnd[t - 1], cmnd[t], cmnd[t + 1]
            t = t + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
        f0[i] = SR / t
    return f0


def segments(db, floor_rel=-30, min_pause=0.25, min_seg=0.15):
    """Speech/pause segmentation on frame dB, threshold relative to the turn's loud frames."""
    ref = np.percentile(db, 95)
    speech = db > ref + floor_rel
    segs, i, n = [], 0, len(speech)
    while i < n:
        if speech[i]:
            j = i
            while j < n and speech[j]:
                j += 1
            segs.append([i, j])
            i = j
        else:
            i += 1
    merged = []
    for s in segs:  # merge across gaps shorter than min_pause
        if merged and (s[0] - merged[-1][1]) * FRAME < min_pause:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return [(a * FRAME, b * FRAME) for a, b in merged if (b - a) * FRAME >= min_seg]


def syllable_rate(x, a, b):
    """Syllables/sec estimate inside [a,b]: peaks in band-limited intensity envelope."""
    seg = x[int(a * SR):int(b * SR)]
    if len(seg) < SR * 0.3:
        return np.nan
    sos = butter(4, [300, 2500], btype="band", fs=SR, output="sos")
    env = np.abs(sosfiltfilt(sos, seg))
    k = int(SR * 0.02)
    env = np.convolve(env, np.ones(k) / k, mode="same")[::int(SR * FRAME)]
    envdb = 20 * np.log10(env + 1e-9)
    peaks, _ = find_peaks(envdb, prominence=2.0, distance=int(0.09 / FRAME),
                          height=np.percentile(envdb, 50))
    return len(peaks) / (b - a)


def semitones(f_hi, f_lo):
    return 12 * np.log2(f_hi / f_lo)


def parse_transcript(run):
    p = os.path.join(run, "TRANSCRIPT.md")
    if not os.path.exists(p):
        return {}
    text = open(p, encoding="utf-8").read()
    out = {}
    for num, spk, body in re.findall(r"\*\*T(\d+) · ([^:*]+):\*\* (.+?)(?=\n\*\*T\d+ |\Z)", text, re.S):
        clean = re.sub(r"\[[^\]]*\]|<[^>]*>", "", body)
        out[int(num)] = (spk.strip(), len(clean.split()))
    return out


def analyse_turn(path):
    x = load(path)
    dur = len(x) / SR
    db = frame_rms_db(x)
    segs = segments(db)
    f0 = yin_f0(x)
    voiced = f0[~np.isnan(f0)]
    speech_frames = np.zeros(len(db), bool)
    for a, b in segs:
        speech_frames[int(a / FRAME):int(b / FRAME)] = True
    act_db = float(np.mean(db[speech_frames])) if speech_frames.any() else float("nan")
    phrases = []
    for a, b in segs:
        fa, fb = int(a / FRAME), int(b / FRAME)
        pf = f0[fa:fb]
        pv = pf[~np.isnan(pf)]
        phrases.append({"start": round(a, 2), "end": round(b, 2),
                        "syl_per_s": round(float(syllable_rate(x, a, b)), 2),
                        "f0_med": round(float(np.median(pv)), 1) if len(pv) > 5 else None,
                        "db": round(float(np.mean(db[fa:fb])), 1)})
    pauses = [segs[i + 1][0] - segs[i][1] for i in range(len(segs) - 1)]
    speech_time = sum(b - a for a, b in segs)
    return {
        "dur_s": round(dur, 2),
        "speech_s": round(speech_time, 2),
        "f0_med": round(float(np.median(voiced)), 1) if len(voiced) else None,
        "f0_range_st": round(float(semitones(np.percentile(voiced, 90), np.percentile(voiced, 10))), 1) if len(voiced) > 20 else None,
        "active_db": round(act_db, 1),
        "n_pauses": len(pauses),
        "pause_med_s": round(float(np.median(pauses)), 2) if pauses else 0.0,
        "lead_silence_s": round(segs[0][0], 2) if segs else None,
        "tail_silence_s": round(dur - segs[-1][1], 2) if segs else None,
        "syl_per_s": round(float(np.nanmedian([p["syl_per_s"] for p in phrases])), 2) if phrases else None,
        "phrase_rate_spread": round(float(np.nanmax([p["syl_per_s"] for p in phrases]) - np.nanmin([p["syl_per_s"] for p in phrases])), 2) if len(phrases) > 1 else 0.0,
        "phrases": phrases,
    }


def spread(vals):
    v = [x for x in vals if x is not None and not np.isnan(x)]
    if len(v) < 2:
        return None
    return {"min": round(min(v), 1), "max": round(max(v), 1), "range": round(max(v) - min(v), 1),
            "sd": round(float(np.std(v)), 2), "cv_pct": round(100 * float(np.std(v) / np.mean(v)), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--glob", default="turns/T*.mp3")
    ap.add_argument("--json")
    args = ap.parse_args()
    files = sorted(glob.glob(os.path.join(args.run, args.glob)))
    if not files:
        sys.exit("no turn files found")
    tx = parse_transcript(args.run)
    rows = []
    for f in files:
        m = re.search(r"T(\d+)", os.path.basename(f))
        num = int(m.group(1)) if m else len(rows) + 1
        r = analyse_turn(f)
        spk, words = tx.get(num, ("?", None))
        r.update({"turn": num, "speaker": spk, "words": words,
                  "wpm": round(words / (r["dur_s"] / 60), 0) if words else None,
                  "wpm_speech_only": round(words / (r["speech_s"] / 60), 0) if words and r["speech_s"] else None})
        rows.append(r)

    print(f"{'turn':>4} {'spk':<8} {'w':>3} {'dur':>5} {'wpm':>4} {'wpm*':>4} {'syl/s':>5} {'f0':>5} {'f0rng':>5} {'dB':>6} {'paus':>4} {'p.med':>5} {'head':>4} {'tail':>4}")
    for r in rows:
        print(f"T{r['turn']:02d}  {r['speaker'][:8]:<8} {r['words'] or '-':>3} {r['dur_s']:5.1f} {r['wpm'] or '-':>4} {r['wpm_speech_only'] or '-':>4} "
              f"{r['syl_per_s'] or 0:5.2f} {r['f0_med'] or 0:5.0f} {r['f0_range_st'] or 0:5.1f} {r['active_db']:6.1f} {r['n_pauses']:>4} {r['pause_med_s']:5.2f} "
              f"{r['lead_silence_s'] or 0:4.2f} {r['tail_silence_s'] or 0:4.2f}")
    print("  wpm* = words per minute of speech only (internal pauses removed); f0rng = 10-90 pct F0 range, semitones")

    summary = {}
    for spk in sorted({r["speaker"] for r in rows}):
        rs = [r for r in rows if r["speaker"] == spk]
        ph = [p for r in rs for p in r["phrases"]]
        summary[spk] = {
            "turns": len(rs),
            "wpm_across_turns": spread([r["wpm"] for r in rs]),
            "wpm_speech_only_across_turns": spread([r["wpm_speech_only"] for r in rs]),
            "syl_rate_across_phrases": spread([p["syl_per_s"] for p in ph]),
            "f0_median_across_turns": spread([r["f0_med"] for r in rs]),
            "f0_range_st_per_turn": spread([r["f0_range_st"] for r in rs]),
            "level_db_across_turns": spread([r["active_db"] for r in rs]),
            "level_db_across_phrases": spread([p["db"] for p in ph]),
        }
    print("\nSPREAD by speaker (range / sd / coefficient of variation):")
    for spk, s in summary.items():
        print(f"  {spk}:")
        for k, v in s.items():
            if isinstance(v, dict):
                print(f"    {k:<30} min {v['min']:>7} max {v['max']:>7} range {v['range']:>6} sd {v['sd']:>6} cv {v['cv_pct']:>5}%")
    if args.json:
        json.dump({"turns": rows, "summary": summary}, open(args.json, "w"), indent=1)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
