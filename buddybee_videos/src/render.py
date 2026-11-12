import sys, os, re, json, math, subprocess, numpy as np, soundfile as sf
from PIL import Image, ImageDraw, ImageFont, ImageFilter
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from content import VIDEOS

REF = "/root/.claude/uploads/821daa29-10a5-5fb0-990a-c2748603fb8a/a7f3f667-buddybee_talking_v2.mp4"
W, H, FPS = 1080, 1920, 30
SEAM = 960            # character layer starts here
TOP = SEAM            # overlay region height
BG = (12, 11, 14)
YEL = (252, 196, 25)
WHITE = (245, 245, 245)
GREY = (150, 150, 155)
CARD = (22, 22, 25)
RED = (255, 82, 82)
F = lambda w, s: ImageFont.truetype(f"{D}/fonts/Poppins-{w}.ttf", s)

def ease_out(x): x = min(max(x, 0), 1); return 1 - (1 - x) ** 3
def ease_back(x):
    x = min(max(x, 0), 1); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2

# ---------- background ----------
def make_bg():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    img = np.zeros((H, W, 3), np.float32) + BG
    g = np.exp(-(((xx - 150) / 700) ** 2 + ((yy - 200) / 500) ** 2)) * 0.10   # faint warm top-left glow
    img += g[..., None] * np.array([255, 200, 40], np.float32)
    g2 = np.exp(-(((xx - 540) / 520) ** 2 + ((yy - 1330) / 430) ** 2)) * 0.13
    img += g2[..., None] * np.array([255, 200, 40], np.float32)
    dots = ((xx % 36) < 2) & ((yy % 36) < 2)
    img[dots] += 7
    return np.clip(img, 0, 255).astype(np.uint8)

# ---------- character footage ----------
def load_char():
    cmd = ["ffmpeg", "-v", "error", "-i", REF, "-vf", f"crop={W}:{H-SEAM}:0:{SEAM}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(raw) // (W * (H - SEAM) * 3)
    return np.frombuffer(raw, np.uint8)[: n * W * (H - SEAM) * 3].reshape(n, H - SEAM, W, 3)

# ---------- text helpers ----------
def rich_line(draw_img, y, line, size, center=W // 2):
    """draw a headline line, *word* segments in yellow bold italic."""
    fb, fi = F("Bold", size), F("BoldItalic", size)
    toks = re.split(r"(\*[^*]+\*)", line)
    segs = [(t[1:-1], fi, YEL) if t.startswith("*") else (t, fb, WHITE) for t in toks if t]
    total = sum(f.getlength(t) for t, f, c in segs)
    x = center - total / 2
    d = ImageDraw.Draw(draw_img)
    for t, f, c in segs:
        d.text((x, y), t, font=f, fill=c); x += f.getlength(t)

def headline_img(lines):
    size = 74
    while any(sum(F("Bold", size).getlength(p.replace("*", "")) for p in [l]) > 960 for l in lines): size -= 4
    img = Image.new("RGBA", (W, 320), (0, 0, 0, 0))
    lh = int(size * 1.18)
    for i, l in enumerate(lines): rich_line(img, 10 + i * lh, l, size)
    return img, 10 + len(lines) * lh

def header_img():
    img = Image.new("RGBA", (W, 60), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    parts = [("BuddyBee", F("SemiBold", 30), YEL, 2.2), ("AI", F("Regular", 30), (200, 200, 200), 2.2),
             (" · AI agents for busy founders", F("Regular", 30), (200, 200, 200), 2.2)]
    def width(t, f, sp): return sum(f.getlength(ch) + sp for ch in t)
    x = W / 2 - sum(width(*p[:2], p[3]) for p in parts) / 2
    for t, f, c, sp in parts:
        for ch in t: d.text((x, 12), ch, font=f, fill=c); x += f.getlength(ch) + sp
    return img

def rrect(d, box, r, fill=None, outline=None, width=2):
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)

def glow(size, r=30, color=YEL, strength=70, pad=40):
    w, h = size
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((pad, pad, pad + w, pad + h), r, outline=color + (strength,), width=10)
    return img.filter(ImageFilter.GaussianBlur(16))

def check(d, cx, cy, s, color, prog=1.0, width=4):
    pts = [(cx - s, cy), (cx - s * 0.3, cy + s * 0.7), (cx + s, cy - s * 0.8)]
    if prog <= 0: return
    if prog < 0.4:
        p = prog / 0.4; d.line([pts[0], (pts[0][0] + (pts[1][0] - pts[0][0]) * p, pts[0][1] + (pts[1][1] - pts[0][1]) * p)], fill=color, width=width)
    else:
        p = (prog - 0.4) / 0.6
        d.line([pts[0], pts[1], (pts[1][0] + (pts[2][0] - pts[1][0]) * p, pts[1][1] + (pts[2][1] - pts[1][1]) * p)], fill=color, width=width, joint="curve")

def with_alpha(img, a):
    if a >= 1: return img
    img = img.copy(); al = img.getchannel("A").point(lambda v: int(v * a)); img.putalpha(al); return img

# ---------- cards: each returns RGBA image (card-local) for local time t ----------
def card_frame(w, h, accent=YEL, glow_strength=60, border=(70, 60, 28)):
    pad = 40
    img = glow((w, h), color=accent, strength=glow_strength, pad=pad)
    d = ImageDraw.Draw(img)
    rrect(d, (pad, pad, pad + w, pad + h), 28, fill=CARD + (255,), outline=border, width=2)
    return img, d, pad

def card_list(c, t):
    items = c["items"]; title = c.get("title")
    w = 760; rowh = 74; top = 70 if title else 30
    h = top + rowh * len(items) + 20
    img, d, p = card_frame(w, h)
    if title: d.text((p + 36, p + 24), title, font=F("Medium", 28), fill=GREY)
    for i, it in enumerate(items):
        a = ease_out((t - 0.25 - i * 0.18) / 0.3)
        if a <= 0: continue
        y = p + top + i * rowh + 14 + (1 - a) * 14
        col = tuple(int(v * a + CARD[k] * (1 - a)) for k, v in enumerate(WHITE))
        dc = tuple(int(v * a + CARD[k] * (1 - a)) for k, v in enumerate(YEL))
        d.ellipse((p + 36, y + 20, p + 48, y + 32), fill=dc)
        d.text((p + 70, y + 4), it, font=F("Medium", 33), fill=col)
        if i < len(items) - 1: d.line((p + 30, p + top + (i + 1) * rowh + 10, p + w - 30, p + top + (i + 1) * rowh + 10), fill=(38, 38, 42), width=2)
    if c.get("badge"):
        s = ease_back((t - 0.2) / 0.4)
        if s > 0:
            f = F("Bold", 44); txt = c["badge"]; bw = max(90, f.getlength(txt) + 44); bh = 76
            bx, by = p + w - bw / 2 + 10, p - 10
            b = Image.new("RGBA", (int(bw) + 4, bh + 4), (0, 0, 0, 0)); bd = ImageDraw.Draw(b)
            bd.rounded_rectangle((2, 2, bw, bh), bh // 2, fill=(236, 54, 60))
            bd.text((bw / 2 + 1, bh / 2 + 3), txt, font=f, fill="white", anchor="mm")
            b = b.resize((max(1, int(b.width * s)), max(1, int(b.height * s))))
            img.alpha_composite(b, (int(bx - b.width / 2 + 20), int(by + 20 - b.height / 2 + 18)))
    return img

def parse_num(v):
    m = re.match(r"^([^\d]*)([\d,\.]+)(.*)$", v)
    if not m: return None
    return m.group(1), m.group(2), m.group(3)

def fmt_count(num_s, frac):
    target = float(num_s.replace(",", ""))
    val = target * frac
    if "." in num_s:
        dec = len(num_s.split(".")[1]); return f"{val:.{dec}f}"
    s = f"{int(round(val)):,}" if "," in num_s else str(int(round(val)))
    return s

def card_stat(c, t):
    col = {"red": RED, "yellow": YEL, "blue": (110, 190, 255)}[c["color"]]
    w, h = 540, 320
    img, d, p = card_frame(w, h, accent=col, glow_strength=55, border=tuple(int(v * 0.45) for v in col))
    v = c["value"]; pn = parse_num(v)
    if pn and not v.startswith("1/"):
        frac = ease_out((t - 0.15) / 0.9); v = pn[0] + fmt_count(pn[1], frac) + pn[2]
    size = 150 if len(c["value"]) <= 4 else 120
    d.text((p + w / 2, p + 135), v, font=F("Bold", size), fill=col, anchor="mm")
    d.text((p + w / 2, p + 262), c["label"], font=F("Regular", 34), fill=(200, 200, 205), anchor="mm")
    return img

def card_chat(c, t):
    w, h = 800, 200
    img, d, p = card_frame(w, h)
    d.text((p + 30, p + 22), "BuddyBeeAI", font=F("Regular", 24), fill=GREY)
    rrect(d, (p + 24, p + 66, p + w - 24, p + h - 24), 22, fill=(16, 16, 18))
    txt = c["text"]; n = int(max(0, t - 0.35) * 22)
    shown = txt[:n]
    f = F("Medium", 31)
    while f.getlength(shown) > w - 170: shown = shown[1:]
    d.text((p + 50, p + 90), shown, font=f, fill=WHITE)
    if n < len(txt) and int(t * 3) % 2 == 0 or n < len(txt):
        cx = p + 54 + f.getlength(shown)
        if int(t * 4) % 2 == 0: d.line((cx, p + 92, cx, p + 132), fill=YEL, width=3)
    sent = n >= len(txt)
    cx, cy = p + w - 70, p + 113
    pulse = 1 + 0.12 * math.sin(t * 8) if sent else 1
    r = 26 * pulse
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=YEL)
    d.polygon([(cx - 8, cy - 11), (cx + 12, cy), (cx - 8, cy + 11)], fill=(20, 20, 20))
    return img

def node(d, cx, cy, title, sub, glowing=False, a=1.0):
    f1, f2 = F("SemiBold", 30), F("Regular", 21)
    w = max(f1.getlength(title), f2.getlength(sub)) + 60; h = 98
    fill = (24, 22, 16) if glowing else (20, 20, 23)
    out = YEL if glowing else (80, 80, 86)
    mix = lambda col: tuple(int(v * a + BG[k] * (1 - a)) for k, v in enumerate(col))
    d.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), 20, fill=mix(fill), outline=mix(out), width=2)
    d.text((cx, cy - 14), title, font=f1, fill=mix(WHITE), anchor="mm")
    d.text((cx, cy + 22), sub, font=f2, fill=mix(GREY), anchor="mm")

def card_flow(c, t):
    w, h = 960, 360
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    A, B, C = (170, 70), (480, 270), (790, 70)
    (n1, s1), (n2, s2), (n3, s3) = c["nodes"]
    a1 = ease_out((t - 0.1) / 0.3); l1 = ease_out((t - 0.45) / 0.45); a2 = ease_out((t - 0.85) / 0.3)
    l2 = ease_out((t - 1.15) / 0.45); a3 = ease_out((t - 1.55) / 0.3)
    def seg(p, q, k):
        if k > 0: d.line((p[0], p[1], p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k), fill=YEL, width=5)
    seg((A[0] + 40, A[1] + 49), (B[0] - 40, B[1] - 49), l1)
    seg((B[0] + 40, B[1] - 49), (C[0] - 40, C[1] + 49), l2)
    if a2 > 0:
        g = Image.new("RGBA", (w, h), (0, 0, 0, 0)); gd = ImageDraw.Draw(g)
        gd.rounded_rectangle((B[0] - 150, B[1] - 55, B[0] + 150, B[1] + 55), 24, fill=YEL + (int(70 * a2),))
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(22)))
    if a1 > 0: node(d, *A, n1, s1, a=a1)
    if a2 > 0: node(d, *B, n2, s2, glowing=True, a=a2)
    if a3 > 0: node(d, *C, n3, s3, a=a3)
    # travelling dot
    if t > 1.9:
        k = ((t - 1.9) % 1.6) / 1.6
        pts = [(A[0] + 40, A[1] + 49), (B[0] - 40, B[1] - 49), (B[0] + 40, B[1] - 49), (C[0] - 40, C[1] + 49)]
        if k < 0.5: p, q, kk = pts[0], pts[1], k * 2
        else: p, q, kk = pts[2], pts[3], (k - 0.5) * 2
        x, y = p[0] + (q[0] - p[0]) * kk, p[1] + (q[1] - p[1]) * kk
        d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(255, 235, 150))
    return img

def card_bars(c, t):
    w, h = 680, 380
    img, d, p = card_frame(w, h)
    d.text((p + 32, p + 26), c["title"], font=F("Regular", 26), fill=GREY)
    v = c["value"]; pn = parse_num(v)
    if pn:
        frac = ease_out((t - 0.2) / 1.2); v = pn[0] + fmt_count(pn[1], frac) + pn[2]
    d.text((p + 32, p + 62), v, font=F("Bold", 50), fill=YEL)
    hs = [0.22, 0.38, 0.52, 0.74, 1.0]; bw = 82; gap = (w - 64 - bw * 5) / 4; base = p + h - 28; maxh = 210
    for i, hh in enumerate(hs):
        k = ease_back((t - 0.25 - i * 0.14) / 0.5)
        if k <= 0: continue
        x = p + 32 + i * (bw + gap); bh = maxh * hh * k
        d.rounded_rectangle((x, base - bh, x + bw, base), 8, fill=YEL if i == 4 else (235, 170, 20))
    return img

def card_compare(c, t):
    w, h = 800, 300
    img, d, p = card_frame(w, h)
    d.text((p + 34, p + 26), c["title"], font=F("Medium", 28), fill=GREY)
    for i, (lab, val, col) in enumerate(c["rows"]):
        y = p + 92 + i * 100
        d.text((p + 34, y), lab, font=F("SemiBold", 30), fill=WHITE)
        k = ease_out((t - 0.3 - i * 0.35) / 0.8)
        d.rounded_rectangle((p + 34, y + 48, p + w - 34, y + 74), 13, fill=(36, 36, 40))
        if k > 0:
            d.rounded_rectangle((p + 34, y + 48, p + 34 + max(26, (w - 68) * val * k), y + 74), 13, fill=YEL if col == "y" else (110, 110, 118))
    return img

def card_pills(c, t):
    w, h = 720, 104
    img = Image.new("RGBA", (w + 80, (h + 26) * len(c["items"]) + 60), (0, 0, 0, 0))
    for i, it in enumerate(c["items"]):
        st = 0.2 + i * 0.75; a = ease_back((t - st) / 0.4)
        if a <= 0: continue
        pi, d, p = card_frame(w, h, glow_strength=int(80 * min(a, 1)))
        rrect(d, (p, p, p + w, p + h), 26, outline=YEL, width=2)
        d.rounded_rectangle((p + 26, p + 28, p + 74, p + 76), 12, fill=YEL)
        check(d, p + 50, p + 52, 12, (20, 20, 20), 1.0, 4)
        d.text((p + 100, p + h / 2), it, font=F("SemiBold", 34), fill=WHITE, anchor="lm")
        check(d, p + w - 48, p + h / 2, 13, (90, 220, 120), ease_out((t - st - 0.35) / 0.35), 4)
        s = min(1, 0.85 + 0.15 * a)
        pi = pi.resize((int(pi.width * s), int(pi.height * s)))
        img.alpha_composite(with_alpha(pi, min(1, a)), (int((w + 80 - pi.width) / 2), int(i * (h + 26) + (1 - min(a, 1)) * 20)))
    return img

def card_brand(c, t):
    img = Image.new("RGBA", (W, 300), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    f = F("Bold", 128)
    parts = [("Buddy", WHITE), ("Bee", YEL), ("AI", WHITE)]
    total = sum(f.getlength(p) for p, _ in parts); x = W / 2 - total / 2
    for p_, col in parts: d.text((x, 20), p_, font=f, fill=col); x += f.getlength(p_)
    sub = "YOUR AI AGENT TEAM"; fs = F("Medium", 30); sp = 7
    sw = sum(fs.getlength(ch) + sp for ch in sub) - sp; x = W / 2 - sw / 2
    a = ease_out((t - 0.5) / 0.4)
    for ch in sub:
        d.text((x, 205), ch, font=fs, fill=tuple(int(v * a) for v in (190, 190, 195)) + (int(255 * a),)); x += fs.getlength(ch) + sp
    return img

def card_cta(c, t):
    w, h = 640, 230
    img = Image.new("RGBA", (w + 80, 500), (0, 0, 0, 0))
    ci, d, p = card_frame(w, h, glow_strength=60)
    d.text((p + 30, p + 24), "Your comment", font=F("Regular", 26), fill=GREY)
    kw = c["kw"]; n = int(max(0, t - 0.3) * 9); shown = kw[:n]
    size = 128 if len(kw) <= 5 else 104
    d.text((p + w / 2, p + 130), shown, font=F("ExtraBold", size), fill=YEL, anchor="mm")
    if n < len(kw) and int(t * 4) % 2 == 0:
        cx = p + w / 2 + F("ExtraBold", size).getlength(shown) / 2 + 6
        d.line((cx, p + 80, cx, p + 180), fill=YEL, width=4)
    img.alpha_composite(ci, (0, 0))
    a = ease_back((t - 0.4 - len(kw) / 9 - 0.3) / 0.45)
    if a > 0:
        pw, ph = 470, 100
        pi, pd, pp = card_frame(pw, ph, glow_strength=40)
        pd.ellipse((pp + 22, pp + 30, pp + 62, pp + 70), fill=YEL)
        pd.polygon([(pp + 36, pp + 39), (pp + 52, pp + 50), (pp + 36, pp + 61)], fill=(20, 20, 20))
        pd.text((pp + 82, pp + 20), "Setup link", font=F("SemiBold", 30), fill=WHITE)
        pd.text((pp + 82, pp + 58), "sent to your DMs", font=F("Regular", 21), fill=GREY)
        check(pd, pp + 82 + F("Regular", 21).getlength("sent to your DMs") + 20, pp + 70, 7, GREY, 1, 2)
        s = min(1, 0.85 + 0.15 * a)
        pi = pi.resize((int(pi.width * s), int(pi.height * s)))
        img.alpha_composite(with_alpha(pi, min(1, a)), (int((w + 80 - pi.width) / 2), int(h + 50 + (1 - min(a, 1)) * 20)))
    return img

CARDS = dict(list=card_list, stat=card_stat, chat=card_chat, flow=card_flow, bars=card_bars,
             compare=card_compare, pills=card_pills, brand=card_brand, cta=card_cta)

# ---------- audio ----------
def make_audio(out, voice, sr, beats, dur):
    n = len(voice); t = np.arange(n) / sr
    # soft music bed: two-chord pad + gentle pulse, 100 bpm
    chords = [[220.0, 261.63, 329.63], [196.0, 246.94, 293.66], [174.61, 220.0, 261.63], [196.0, 246.94, 329.63]]
    bar = 60 / 100 * 4
    music = np.zeros(n, np.float32)
    for i, ch in enumerate(chords * int(dur / bar / 4 + 2)):
        s0 = int(i * bar * sr); s1 = min(n, int((i + 1) * bar * sr))
        if s0 >= n: break
        tt = np.arange(s1 - s0) / sr
        env = np.minimum(1, tt / 0.6) * np.minimum(1, (bar - tt) / 0.6)
        for f in ch:
            music[s0:s1] += (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * f * 2 * tt)) * env * 0.05
    beat = 60 / 100
    for k in range(int(dur / beat) + 1):  # soft kick
        s0 = int(k * beat * sr); L = int(0.18 * sr)
        if s0 + L > n: break
        tt = np.arange(L) / sr
        music[s0:s0 + L] += np.sin(2 * np.pi * (55 + 60 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 18) * 0.18
    # duck music under voice
    env = np.abs(voice); k = int(0.15 * sr)
    env = np.convolve(env, np.ones(k) / k, mode="same")
    duck = 1 - 0.55 * np.clip(env / (env.max() * 0.25), 0, 1)
    music *= duck * 0.55
    # pop sfx at each card start
    sfx = np.zeros(n, np.float32); rng = np.random.default_rng(1)
    for b0, _ in beats:
        s0 = int((b0 + 0.12) * sr); L = int(0.09 * sr)
        if s0 + L > n: continue
        tt = np.arange(L) / sr
        sfx[s0:s0 + L] += np.sin(2 * np.pi * (900 - 4000 * tt) * tt) * np.exp(-tt * 45) * 0.22
        wl = int(0.25 * sr); s1 = max(0, s0 - wl)
        noise = rng.standard_normal(s0 - s1).astype(np.float32)
        noise = np.convolve(noise, np.ones(30) / 30, mode="same")
        sfx[s1:s0] += noise * np.linspace(0, 1, s0 - s1) ** 2 * 0.25
    mix = voice * 1.0 + music + sfx
    mix = mix / max(1e-6, np.abs(mix).max()) * 0.95
    sf.write(out, np.stack([mix, mix], 1), sr)

# ---------- main ----------
def render(v, outdir, preview=None):
    bdir = f"{D}/build/{v['slug']}"
    tm = json.load(open(f"{bdir}/timing.json")); sr = tm["sr"]
    voice, _ = sf.read(f"{bdir}/voice.wav", dtype="float32")
    beats = tm["beats"]; dur = tm["dur"]; N = int(dur * FPS)
    make_audio(f"{bdir}/mix.wav", voice, sr, beats, dur)

    bg = make_bg(); char = load_char(); nc = len(char)
    mask = np.ones((H - SEAM, 1, 1), np.float32); fe = 90
    mask[:fe, 0, 0] = np.linspace(0, 1, fe) ** 1.3
    bg_low = bg[SEAM:].astype(np.float32)
    bg_top = Image.fromarray(bg[:TOP]).convert("RGBA")
    bg_top.alpha_composite(header_img(), (0, 58))
    heads = [headline_img(b["head"]) for b in v["beats"]]

    # visual beat i active from (start_i - 0.15) until next
    vstarts = [max(0, b[0] - 0.15) for b in beats]; vstarts[0] = 0
    frames = range(N) if preview is None else preview
    out = f"{outdir}/{v['slug']}.mp4" if preview is None else None
    if out:
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                               "-i", f"{bdir}/mix.wav", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    shots = []
    for fi in frames:
        t = fi / FPS
        bi = max(i for i, s in enumerate(vstarts) if s <= t); lt = t - vstarts[bi]
        b = v["beats"][bi]
        # character: continuous ping-pong
        k = fi % (2 * nc - 2); ci = k if k < nc else 2 * nc - 2 - k
        low = (bg_low * (1 - mask) + char[ci].astype(np.float32) * mask).astype(np.uint8)
        top = bg_top.copy()
        himg, hh = heads[bi]
        a = ease_out(lt / 0.3)
        top.alpha_composite(with_alpha(himg, a), (0, int(190 + (1 - a) * 30)))
        cimg = CARDS[b["card"]["type"]](b["card"], lt)
        s = 0.9 + 0.1 * ease_back((lt - 0.12) / 0.35); ca = ease_out((lt - 0.12) / 0.25)
        if ca > 0:
            if s != 1: cimg = cimg.resize((max(1, int(cimg.width * s)), max(1, int(cimg.height * s))))
            cy0 = 190 + hh + 40
            area_c = (cy0 + 900) / 2
            y = int(area_c - cimg.height / 2)
            if b["card"]["type"] == "brand": y = int(area_c - cimg.height / 2 - 60)
            if b["card"]["type"] == "cta": y = cy0 - 30
            y = max(cy0 - 30, y)
            top.alpha_composite(with_alpha(cimg, ca), (int(W / 2 - cimg.width / 2), y))
        frame = np.concatenate([np.asarray(top.convert("RGB")), low], 0)
        if out: ff.stdin.write(frame.tobytes())
        else: shots.append(Image.fromarray(frame))
    if out:
        ff.stdin.close(); ff.wait(); return out
    return shots

if __name__ == "__main__":
    outdir = sys.argv[1]; which = sys.argv[2] if len(sys.argv) > 2 else "all"
    os.makedirs(outdir, exist_ok=True)
    for v in VIDEOS:
        if which != "all" and not v["slug"].startswith(which): continue
        if len(sys.argv) > 3 and sys.argv[3] == "preview":
            tm = json.load(open(f"{D}/build/{v['slug']}/timing.json"))
            fr = [int((b[0] + 1.6) * FPS) for b in tm["beats"]]
            shots = render(v, outdir, fr)
            sm = [s.resize((360, 640)) for s in shots]
            g = Image.new("RGB", (360 * 5, 640 * 2))
            for i, s in enumerate(sm): g.paste(s, ((i % 5) * 360, (i // 5) * 640))
            g.save(f"{outdir}/{v['slug']}_preview.png"); print("preview", v["slug"])
        else:
            print("rendered", render(v, outdir), flush=True)
