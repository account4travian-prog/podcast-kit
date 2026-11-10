"""Drama 3 renderer: SCENE mode (joint multi-speaker takes) or TURN mode.

Why scene mode: early versions rendered every turn in its own request, so the engine
performed each line cold, never hearing what it was answering. Drama 3
renders a whole multi-speaker scene in one request (`<|speaker:n|>` tags +
an array of voice IDs; docs.fish.audio/features/text-to-speech/drama-3), and
the technical report caps a take at ~190 s (4,096 frames). Scene mode groups
consecutive turns into takes well under that cap, breaking BEFORE host turns
that open a new question, so each take is a stretch of real back-and-forth.

Direct HTTP; no fish.py connector needed. Key from FISH_API_KEY.

  python tools/render_drama3.py RUN_DIR --voices voices.json --mode scene [--takes 2] [--dry-run]

voices.json: {"Host": "voice-model-id", "Alex": "voice-model-id"}
Script: RUN_DIR/TRANSCRIPT.md in the room format (**T1 · Host:** text...),
tags intact. Output: RUN_DIR/render-<mode>/ (takes, manifest.json, mix.mp3).

Pricing note (docs, read 2026-11-09): drama-3 is billed per UTF-8 byte of
input: $10/M bytes promo through 2026-11-30, $50/M after. A 5-minute room is
~5 KB, i.e. ~$0.05 per take at promo, ~$0.25 after.
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
import numpy as np

API = os.environ.get("FISH_API_URL", "https://api.fish.audio/v1/tts")  # override only for local testing
SR = 44100
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from remix_turns import trim, master  # noqa: E402


def parse(run):
    t = open(os.path.join(run, "TRANSCRIPT.md"), encoding="utf-8").read()
    out = []
    for n, s, b in re.findall(r"\*\*T(\d+) · ([^:*]+):\*\* (.+?)(?=\n\*\*T\d+ |\Z)", t, re.S):
        out.append((int(n), s.strip(), " ".join(b.split())))
    return sorted(out)


def plan_scenes(turns, host, max_chars):
    """Greedy grouping; a scene may only be cut before a host turn, and a cut is
    taken when the next host-led exchange would push the scene past max_chars."""
    scenes, cur = [], []
    for i, t in enumerate(turns):
        if cur and t[1] == host:
            # size of the exchange this host turn opens (host turn + replies until next host turn)
            j, add = i, 0
            while j < len(turns) and (j == i or turns[j][1] != host):
                add += len(turns[j][2]) + 16
                j += 1
            if sum(len(x[2]) + 16 for x in cur) + add > max_chars:
                scenes.append(cur)
                cur = []
        cur.append(t)
    if cur:
        scenes.append(cur)
    # never leave a lone closing turn performed cold: fold it into the previous take
    # if that stays under the hard cap (~2,200 chars ~ 150 s, inside the ~190 s take limit)
    if len(scenes) > 1 and len(scenes[-1]) == 1 and \
            sum(len(x[2]) + 16 for x in scenes[-2] + scenes[-1]) <= 2200:
        last = scenes.pop()
        scenes[-1] = scenes[-1] + last
    return scenes


def build_text(scene, order):
    return "".join(f"<|speaker:{order.index(s)}|>{body}" for _, s, body in scene)


def post(body, key, out_path, retries=3):
    import requests
    for k in range(retries):
        r = requests.post(API, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                        "model": "drama-3"}, data=json.dumps(body), timeout=600)
        if r.status_code == 200 and len(r.content) > 1000:
            open(out_path, "wb").write(r.content)
            return
        print(f"  HTTP {r.status_code}: {r.text[:300]}", file=sys.stderr)
        time.sleep(3 * (k + 1))
    sys.exit("render failed")


def load_wav(p):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--voices", required=True)
    ap.add_argument("--mode", choices=["scene", "turn"], default="scene")
    ap.add_argument("--host", default="Host")
    ap.add_argument("--max-chars", type=int, default=1400,
                    help="scene size cap; ~1400 chars of this show's dialogue renders ~90-110 s, under the ~190 s take cap")
    ap.add_argument("--temperature", type=float, default=0.8,
                    help="API default 0.7; higher = more varied delivery. Sweep 0.7/0.8/0.9 on one scene first")
    ap.add_argument("--top-p", type=float, default=0.8)
    ap.add_argument("--takes", type=int, default=1, help="takes per scene, for picking (cheap: billed on input bytes)")
    ap.add_argument("--scene-gap", type=float, default=0.35)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    voices = json.load(open(a.voices))
    order = list(voices)  # speaker index = order in voices.json
    turns = parse(a.run)
    missing = {s for _, s, _ in turns} - set(order)
    if missing:
        sys.exit(f"no voice for {missing}")
    units = plan_scenes(turns, a.host, a.max_chars) if a.mode == "scene" else [[t] for t in turns]

    outdir = os.path.join(a.run, f"render-{a.mode}")
    os.makedirs(outdir, exist_ok=True)
    key = os.environ.get("FISH_API_KEY")
    if not key and not a.dry_run:
        sys.exit("set FISH_API_KEY (or use --dry-run)")

    manifest, total_bytes = {"mode": a.mode, "temperature": a.temperature, "top_p": a.top_p, "units": []}, 0
    for u, unit in enumerate(units, 1):
        spk_in = [s for s in order if any(t[1] == s for t in unit)]
        if a.mode == "scene":
            text = build_text(unit, spk_in)
            ref = [voices[s] for s in spk_in]
        else:
            text, ref = unit[0][2], voices[unit[0][1]]
        body = {"text": text, "reference_id": ref, "format": "wav", "sample_rate": SR,
                "temperature": a.temperature, "top_p": a.top_p, "latency": "normal", "normalize": True}
        nbytes = len(text.encode("utf-8"))
        total_bytes += nbytes * a.takes
        entry = {"unit": u, "turns": [t[0] for t in unit], "speakers": spk_in, "bytes": nbytes, "takes": []}
        print(f"U{u:02d} turns T{unit[0][0]}-T{unit[-1][0]} ({len(unit)} turns, {nbytes} bytes, speakers {spk_in})")
        for k in range(1, a.takes + 1):
            p = os.path.join(outdir, f"U{u:02d}-take{k}.wav")
            if not a.dry_run and not os.path.exists(p):
                post(body, key, p)
            if os.path.exists(p):
                d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                          "csv=p=0", p], capture_output=True, text=True).stdout)
                entry["takes"].append({"file": os.path.basename(p), "dur_s": round(d, 2),
                                       "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()})
                print(f"   take{k}: {d:.1f}s")
        entry["request"] = body
        manifest["units"].append(entry)

    cost_promo, cost_list = total_bytes / 1e6 * 10, total_bytes / 1e6 * 50
    manifest["billed_bytes"] = total_bytes
    print(f"\n{total_bytes} bytes -> ~${cost_promo:.3f} at promo ($10/M, to 2026-11-30), ~${cost_list:.3f} at list ($50/M)")
    json.dump(manifest, open(os.path.join(outdir, "manifest.json"), "w"), indent=1)
    if a.dry_run:
        print("dry run: wrote manifest with request bodies, no API calls")
        return

    # assemble take1 of each unit (pick other takes by renaming / editing manifest)
    parts = []
    for i, e in enumerate(manifest["units"]):
        if i:
            parts.append(np.zeros(int(a.scene_gap * SR), np.float32))
        parts.append(trim(load_wav(os.path.join(outdir, e["takes"][0]["file"]))))
    raw = os.path.join(outdir, "mix.f32")
    np.concatenate(parts).astype(np.float32).tofile(raw)
    master(raw, os.path.join(outdir, "mix.mp3"))
    os.remove(raw)
    print(f"wrote {os.path.join(outdir, 'mix.mp3')} -- now run tools/prosody_report.py on the takes")


if __name__ == "__main__":
    main()
