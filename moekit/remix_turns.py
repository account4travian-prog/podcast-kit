"""Re-assemble per-turn renders into a room mix, fixing three render-side
defects found in early production testing:

  1. LEVEL: one gain per SPEAKER (not per turn) so every voice sits at the same
     loudness while each speaker's own turn-to-turn variation is preserved.
     (In testing, one guest rendered 7-8 dB hotter than the host in every turn
     of the final mix. Per-turn normalisation would fix that too, but it would
     also flatten the very variation the delivery ruling asks for.)
  2. JOINS: trims each render's own silent head/tail (Drama 3 leaves ~0.1 s
     head and ~0.4-0.75 s tail), then inserts a per-transition gap. A fixed
     350 ms join + render tails made every turn change ~1.0 s of dead air in
     testing; conversational turn gaps cluster around 0.2 s.
  3. TIMBRE (optional): a presence EQ for a speaker whose render is band-limited
     (in testing, one guest: 95% spectral rolloff ~1.8 kHz, -25 dB above
     3.4 kHz, i.e. telephone bandwidth). EQ cannot invent missing detail; it is
     a stop-gap until the voice is recast.

Usage:
  python remix_turns.py ROOM_DIR --out remix.mp3 --gaps gaps.json [--eq Alex]
Gaps file: {"default": 0.22, "T02": 0.45, ...}  (seconds of silence BEFORE that turn)
"""
import argparse, glob, json, os, re, subprocess, tempfile
import numpy as np

SR = 44100


def load(p):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def eq(x, chain):
    with tempfile.TemporaryDirectory() as d:
        a, b = os.path.join(d, "a.f32"), os.path.join(d, "b.f32")
        x.astype(np.float32).tofile(a)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", a,
                        "-af", chain, "-f", "f32le", b], check=True)
        return np.fromfile(b, np.float32)


PRESENCE = "highpass=f=80,equalizer=f=250:t=q:w=1.0:g=-2,equalizer=f=4000:t=q:w=0.9:g=5,highshelf=f=7000:g=3"


def frame_db(x, n=1024, hop=512):
    fr = np.lib.stride_tricks.sliding_window_view(x, n)[::hop]
    return 20 * np.log10(np.sqrt((fr ** 2).mean(1)) + 1e-9), hop


def trim(x, pad=0.04, rel=-35):
    db, hop = frame_db(x)
    on = np.where(db > np.percentile(db, 95) + rel)[0]
    if len(on) == 0:
        return x
    a = max(0, on[0] * hop - int(pad * SR))
    b = min(len(x), (on[-1] + 2) * hop + int(pad * SR))
    y = x[a:b].copy()
    f = int(0.015 * SR)
    y[:f] *= np.linspace(0, 1, f)
    y[-f:] *= np.linspace(1, 0, f)
    return y


def active_db(x):
    db, _ = frame_db(x)
    act = db[db > np.percentile(db, 95) - 20]
    return 10 * np.log10(np.mean(10 ** (act / 10)))


def master(raw_f32, out, I=-16.0, TP=-1.0):
    """Two-pass LINEAR loudnorm: one static gain for the whole programme.
    Single-pass loudnorm (what build_sample3.py used) runs in DYNAMIC mode,
    i.e. a slow compressor that flattens loud-vs-quiet delivery."""
    src = ["-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", raw_f32]
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *src, "-af",
                        f"loudnorm=I={I}:TP={TP}:LRA=20:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = json.loads(r.stderr[r.stderr.rindex("{"):r.stderr.rindex("}") + 1])
    gain_needed = I - float(m["input_i"])
    headroom = TP - float(m["input_tp"])
    if gain_needed > headroom:  # linear mode would be refused; limit peaks first, then gain
        print(f"note: {gain_needed:.1f} dB gain needed, {headroom:.1f} dB peak headroom; limiting peaks")
    g = gain_needed
    for _ in range(4):  # peak limiting costs loudness; iterate the static gain until within 0.3 LU
        af = (f"alimiter=limit={min(1.0, 10 ** ((TP - g - 0.3) / 20)):.4f}:attack=2:release=50:level=false,"
              if g > headroom else "")
        af += f"volume={g:.2f}dB"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-af", af, "-ar", str(SR),
                        "-codec:a", "libmp3lame", "-b:a", "160k", out], check=True)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", out, "-af", "ebur128", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
        got = float(re.findall(r"^\s+I:\s+(-?[\d.]+)", r, re.M)[-1])
        if abs(got - I) <= 0.3:
            break
        g += I - got
    gain_needed = g
    print(f"mastered: input {m['input_i']} LUFS / TP {m['input_tp']} -> static gain {gain_needed:+.2f} dB")


def speakers_from_transcript(run):
    t = open(os.path.join(run, "TRANSCRIPT.md"), encoding="utf-8").read()
    return {int(n): s.strip() for n, s in re.findall(r"\*\*T(\d+) · ([^:*]+):\*\*", t)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gaps")
    ap.add_argument("--eq", action="append", default=[], help="speaker name to receive presence EQ")
    ap.add_argument("--target", type=float, default=-20.0, help="active-speech dBFS per speaker before mastering")
    a = ap.parse_args()

    spk = speakers_from_transcript(a.run)
    gaps = json.load(open(a.gaps)) if a.gaps else {}
    files = sorted(glob.glob(os.path.join(a.run, "turns", "T*.mp3")))
    turns = []
    for f in files:
        n = int(re.search(r"T(\d+)", os.path.basename(f)).group(1))
        x = trim(load(f))
        if spk[n] in a.eq:
            x = eq(x, PRESENCE)
        turns.append((n, spk[n], x))

    # one gain per speaker: median active level across that speaker's turns -> target
    gain = {}
    for s in sorted(set(spk.values())):
        lv = [active_db(x) for n, sp, x in turns if sp == s]
        gain[s] = a.target - float(np.median(lv))
        print(f"{s}: median active {np.median(lv):.1f} dBFS (turn spread {min(lv):.1f}..{max(lv):.1f}) -> gain {gain[s]:+.1f} dB")

    out, log = [], []
    for i, (n, s, x) in enumerate(turns):
        if i:
            g = float(gaps.get(f"T{n:02d}", gaps.get("default", 0.22)))
            out.append(np.zeros(int(g * SR), np.float32))
            log.append(f"  gap {g:.2f}s -> T{n:02d}")
        out.append(x * 10 ** (gain[s] / 20))
    y = np.concatenate(out)
    with tempfile.TemporaryDirectory() as d:
        raw = os.path.join(d, "mix.f32")
        y.astype(np.float32).tofile(raw)
        master(raw, a.out)
    print("\n".join(log))
    print(f"wrote {a.out}  ({len(y) / SR:.1f}s before mastering)")


if __name__ == "__main__":
    main()
