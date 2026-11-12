import sys, os, json, numpy as np, soundfile as sf
sys.path.insert(0, os.path.dirname(__file__))
from content import VIDEOS
from kokoro_onnx import Kokoro
D = os.path.dirname(__file__)
k = Kokoro(f"{D}/tts/kokoro-v1.0.onnx", f"{D}/tts/voices-v1.0.bin")
VOICE = sys.argv[1] if len(sys.argv) > 1 else "am_michael"
TARGET = 59.0
LEAD, GAP, TAIL = 0.25, 0.32, 1.2
for v in VIDEOS:
    out = f"{D}/build/{v['slug']}"; os.makedirs(out, exist_ok=True)
    speed = 1.0
    for attempt in range(3):
        clips = []
        for b in v["beats"]:
            s, sr = k.create(b["say"], voice=VOICE, speed=speed, lang="en-us")
            # trim leading/trailing silence
            idx = np.where(np.abs(s) > 0.01)[0]
            s = s[max(0, idx[0] - 600): idx[-1] + 1200]
            clips.append(s)
        total = LEAD + sum(len(c) / sr for c in clips) + GAP * (len(clips) - 1) + TAIL
        if abs(total - TARGET) < 1.5: break
        speed = round(speed * total / TARGET, 3)
    t = LEAD; starts = []; parts = [np.zeros(int(LEAD * sr), np.float32)]
    for i, c in enumerate(clips):
        starts.append([t, t + len(c) / sr]); parts.append(c.astype(np.float32)); t += len(c) / sr
        g = GAP if i < len(clips) - 1 else TAIL
        parts.append(np.zeros(int(g * sr), np.float32)); t += g
    voice = np.concatenate(parts)
    sf.write(f"{out}/voice.wav", voice, sr)
    json.dump({"sr": sr, "beats": starts, "dur": len(voice) / sr, "speed": speed}, open(f"{out}/timing.json", "w"))
    print(v["slug"], "speed", speed, "dur %.1f" % (len(voice) / sr))
