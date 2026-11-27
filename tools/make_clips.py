#!/usr/bin/env python3
"""Generate the question clips once (any machine). Uses a local TTS: espeak-ng / piper / macOS say / Windows SAPI.
  python tools/make_clips.py [--dataset frames] [--tts auto|espeak|piper|say|sapi] [--limit N]
Writes audio/<ds>/<id>.wav and audio/_fixed/clarify.wav (spoken text = the speech-normalised question)."""
import argparse, json, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from me4bench.speech import CLARIFY_REPLY  # noqa: E402


def synth(tts, text, out: Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    if tts == "espeak":
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        subprocess.run([exe, "-w", str(out), text], check=True)
    elif tts == "piper":
        subprocess.run(["piper", "--model", "en_US-lessac-medium", "--output_file", str(out)], input=text.encode(), check=True)
    elif tts == "say":
        with tempfile.TemporaryDirectory() as t:
            aiff = Path(t) / "a.aiff"
            subprocess.run(["say", "-o", str(aiff), text], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", str(aiff), str(out)], check=True)
    elif tts == "sapi":
        ps = f"Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;$s.SetOutputToWaveFile('{out}');$s.Speak([Console]::In.ReadToEnd());$s.Dispose()"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text.encode(), check=True)


def pick():
    if shutil.which("espeak-ng") or shutil.which("espeak"): return "espeak"
    if shutil.which("piper"): return "piper"
    if sys.platform == "darwin" and shutil.which("say"): return "say"
    if sys.platform == "win32": return "sapi"
    sys.exit("No TTS found. Install espeak-ng (apt/brew) or piper, or pass --tts.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data"); ap.add_argument("--audio", default="audio")
    ap.add_argument("--dataset"); ap.add_argument("--tts", default="auto"); ap.add_argument("--limit", type=int)
    ap.add_argument("--test-only", action="store_true", help="only the test split")
    a = ap.parse_args()
    tts = pick() if a.tts == "auto" else a.tts
    synth(tts, CLARIFY_REPLY, Path(a.audio) / "_fixed" / "clarify.wav")
    for p in sorted(Path(a.root, "_manifest").glob("*.jsonl")):
        if a.dataset and p.stem != a.dataset: continue
        n = 0
        for line in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if a.test_only and r["split"] != "test": continue
            out = Path(a.audio) / r["ds"] / f"{r['id']}.wav"
            if out.exists(): continue
            synth(tts, r["spoken"] or r["question"], out)
            n += 1
            if a.limit and n >= a.limit: break
        print(f"{p.stem}: {n} new clips")


main()
