"""Make the narrated explainer video: python -m scripts.make_video

Renders 1920x1080 slides with Pillow, narrates each with Windows text-to-speech (System.Speech),
and joins them into video/dockergpt-memory-explainer.mp4 with ffmpeg (from imageio-ffmpeg).
Edit SCENES to change wording, then re-run.
"""

import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "video"
WORK = OUT / "build"
W, H = 1920, 1080
BG, PANEL, INK, QUIET, ACCENT, GREEN, RED = "#0e1120", "#171b30", "#f4f5fb", "#a3a9c2", "#8b7cf6", "#3ecf8e", "#ff6b6b"
FONTS = Path("C:/Windows/Fonts")
VOICE = "Microsoft Zira Desktop"


def font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    name = "consola.ttf" if mono else ("segoeuib.ttf" if bold else "segoeui.ttf")
    path = FONTS / name
    if not path.exists():
        path = FONTS / "segoeui.ttf"
    return ImageFont.truetype(str(path), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        test = (line + " " + w).strip()
        if draw.textlength(test, font=f) <= width:
            line = test
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def base(kicker: str, title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.text((120, 90), kicker.upper(), font=font(30, bold=True), fill=ACCENT)
    y = 140
    for line in wrap(d, title, font(64, bold=True), W - 240):
        d.text((120, y), line, font=font(64, bold=True), fill=INK)
        y += 80
    d.text((120, H - 70), "DockerGPT Memory  ·  Hacktoberfest 2026", font=font(24), fill=QUIET)
    return img, d


def bullets(d, items: list[str], y: int = 330, size: int = 40, x: int = 140, width: int = W - 300) -> int:
    f = font(size)
    for item in items:
        d.ellipse([x, y + size // 2 - 8, x + 16, y + size // 2 + 8], fill=ACCENT)
        for i, line in enumerate(wrap(d, item, f, width)):
            d.text((x + 40, y), line, font=f, fill=INK)
            y += int(size * 1.35)
        y += 18
    return y


def card(d, x, y, w, h, title, lines, color=ACCENT, fill=PANEL):
    d.rounded_rectangle([x, y, x + w, y + h], radius=18, fill=fill, outline=color, width=3)
    d.text((x + 28, y + 22), title, font=font(34, bold=True), fill=INK)
    yy = y + 76
    for ln in lines:
        for part in wrap(d, ln, font(28), w - 56):
            d.text((x + 28, yy), part, font=font(28), fill=QUIET)
            yy += 38


def arrow(d, x1, y1, x2, y2, color=QUIET):
    d.line([x1, y1, x2, y2], fill=color, width=4)
    import math

    a = math.atan2(y2 - y1, x2 - x1)
    p = [(x2, y2), (x2 - 22 * math.cos(a - 0.4), y2 - 22 * math.sin(a - 0.4)),
         (x2 - 22 * math.cos(a + 0.4), y2 - 22 * math.sin(a + 0.4))]
    d.polygon(p, fill=color)


# ---------------- slides ----------------

def s_title():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ACCENT)
    d.text((120, 300), "DockerGPT Memory", font=font(120, bold=True), fill=INK)
    d.text((124, 460), "An agent that remembers every container fix", font=font(54), fill=ACCENT)
    d.text((124, 560), "so no team solves the same failure twice.", font=font(54), fill=INK)
    d.text((124, 760), "Docker  ·  Kepler  ·  LLM (Gemma)  ·  JarvisLabs  ·  Temporal  ·  NudgeBee", font=font(36), fill=QUIET)
    return img


def s_problem():
    img, d = base("The problem", "Teams re-solve the same container failures again and again")
    bullets(d, [
        "The usual suspects: port already in use, out-of-memory kill, missing environment variable, "
        "service starting before its database, bad volume permission.",
        "Someone searches logs, asks in chat, tries three fixes, and moves on.",
        "The fix lives in one person's head or a buried Slack thread.",
        "When they leave, or a newcomer hits the same error, the work starts again.",
    ])
    return img


def s_why():
    img, d = base("Why existing tools don't help", "Same root cause, completely different error text")
    card(d, 120, 330, 820, 200, "Log tools", ["Show what happened.", "Not what fixed it last time."])
    card(d, 980, 330, 820, 200, "General chatbots", ["Generic answers.", "No knowledge of your stack or past incidents."])
    d.text((120, 590), "Two apps, one cause (no database password):", font=font(36, bold=True), fill=INK)
    d.rounded_rectangle([120, 650, 1800, 740], radius=14, fill=PANEL)
    d.text((150, 672), "Python:  fe_sendauth: no password supplied", font=font(36, mono=True), fill=GREEN)
    d.rounded_rectangle([120, 760, 1800, 850], radius=14, fill=PANEL)
    d.text((150, 782), "Node:    SASL: SCRAM-SERVER-FIRST-MESSAGE: client password must be a string", font=font(32, mono=True), fill=GREEN)
    d.text((120, 890), "No shared keywords. Connecting them needs understanding, not search.", font=font(36), fill=ACCENT)
    return img


def s_idea():
    img, d = base("Our idea", "Every resolved failure becomes a lesson")
    parts = [("Symptom", "error, exit code, restart pattern"), ("Context", "image, service, limits, env names"),
             ("Evidence", "key log lines, resource stats"), ("Root cause", "in plain language"),
             ("Fix", "the change that worked, and if it held"), ("Outcome", "time to resolve, who approved")]
    for i, (t, s) in enumerate(parts):
        x = 120 + (i % 3) * 570
        y = 330 + (i // 3) * 250
        card(d, x, y, 530, 210, t, [s])
    d.text((120, 860), "When a new container fails, the agent checks these lessons first, and explains why one matches.",
           font=font(34), fill=INK)
    return img


def s_flow():
    img, d = base("How it works", "Eight steps; a human approves every fix")
    steps = [("1 Detect", "Docker events"), ("2 Investigate", "Kepler"), ("3 Retrieve", "Qdrant memory"),
             ("4 Reason", "Gemma on JarvisLabs"), ("5 Approve", "a human"), ("6 Apply", "backup, then fix"),
             ("7 Verify", "25 s health check"), ("8 Learn", "store the lesson")]
    for i, (t, s) in enumerate(steps):
        x = 120 + (i % 4) * 430
        y = 340 + (i // 4) * 300
        color = GREEN if t.startswith("5") else ACCENT
        card(d, x, y, 380, 200, t, [s], color=color)
        if i % 4 != 3:
            arrow(d, x + 382, y + 100, x + 428, y + 100)
    d.line([(1600, 540), (1600, 590), (310, 590)], fill=QUIET, width=4)  # wrap from step 4 down to step 5
    arrow(d, 310, 590, 310, 638)
    d.text((120, 960), "Failed health check → automatic rollback, and the failed fix is remembered so it is never suggested again.",
           font=font(30), fill=RED)
    return img


def s_arch():
    img, d = base("Architecture", "One durable Temporal workflow per incident")
    def b(x, y, w, h, t, s, color=QUIET, fill=PANEL):
        d.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=fill, outline=color, width=3)
        d.text((x + 22, y + 18), t, font=font(30, bold=True), fill=INK)
        yy = y + 62
        for line in s:
            d.text((x + 22, yy), line, font=font(24), fill=QUIET)
            yy += 32
    b(120, 300, 360, 150, "Docker host", ["stack A (Python)", "stack B (Node)"])
    b(560, 300, 340, 150, "Watcher", ["Docker events:", "die, oom, unhealthy"])
    b(980, 300, 340, 150, "API (FastAPI)", ["failures, incidents,", "search, metrics"])
    b(1400, 300, 400, 150, "Dashboard", ["human reviews", "and approves"])
    b(120, 540, 360, 160, "Kepler", ["local SRE agent:", "diagnoses + remembers"])
    b(560, 540, 760, 160, "Temporal worker: ResolveAndLearn", ["Kepler → memory → reason → approve", "→ apply → verify → learn (or rollback)"], color=ACCENT, fill="#221d45")
    b(1400, 540, 400, 160, "JarvisLabs", ["Gemma 4 31B (vLLM)", "match, confidence, fix"])
    d.rounded_rectangle([120, 780, 1800, 1000], radius=18, fill="#13172a", outline=QUIET, width=2)
    d.text((150, 795), "NudgeBee platform (its own infrastructure)", font=font(28, bold=True), fill=ACCENT)
    b(150, 845, 500, 130, "Postgres", ["incidents + NudgeBee RCA history"])
    b(710, 845, 500, 130, "Qdrant", ["incident memory vectors"])
    b(1270, 845, 500, 130, "Temporal server", ["state, history, audit trail"])
    arrow(d, 480, 375, 560, 375); arrow(d, 900, 375, 980, 375); arrow(d, 1320, 375, 1400, 375); arrow(d, 1400, 375, 1320, 375)
    arrow(d, 1150, 450, 1150, 540); arrow(d, 560, 620, 480, 620); arrow(d, 1320, 620, 1400, 620)
    arrow(d, 640, 540, 300, 450); arrow(d, 760, 700, 760, 845); arrow(d, 960, 700, 960, 845); arrow(d, 1250, 700, 1450, 845)
    return img


def s_tech():
    img, d = base("Every technology has a real job", "What each one does here")
    rows = [("Temporal", "durable workflow: survives crashes, waits for approval as a signal, rolls back, keeps an audit trail"),
            ("JarvisLabs", "hosts Gemma 4 31B privately on our own GPU deployment, scales workers to control cost"),
            ("NudgeBee", "the platform it runs on, and its historical RCA reports seed memory on day one"),
            ("Kepler", "local SRE agent: independent diagnosis of each failure, and it remembers every fix"),
            ("Docker", "the trigger (events, logs, stats) and the fix target (compose up on one service)"),
            ("LLM (Gemma)", "decides if causes match, states confidence, explains, adapts the old fix")]
    y = 320
    for name, text in rows:
        d.rounded_rectangle([120, y, 1800, y + 104], radius=14, fill=PANEL)
        d.text((150, y + 30), name, font=font(36, bold=True), fill=ACCENT)
        lines = wrap(d, text, font(30), 1300)
        yy = y + (52 - 20 * len(lines)) + 6
        for line in lines:
            d.text((470, yy), line, font=font(30), fill=INK)
            yy += 40
        y += 120
    return img


def s_demo():
    img, d = base("The demo", "The same failure, solved twice")
    card(d, 120, 330, 820, 420, "1. Stack A (Python)", [
        "Deployed without its database password.",
        "Memory has no matching lesson, and the agent says so.",
        "Fresh diagnosis: add DB_PASSWORD.",
        "Human approves → applied → verified → stored."], color=QUIET)
    card(d, 980, 330, 820, 420, "2. Stack B (Node)", [
        "Same cause, different error text.",
        "Agent finds the stack A lesson and explains why it matches.",
        "Adapts the fix to this stack: PGPASSWORD.",
        "Approved → resolved, much faster."], color=GREEN)
    d.text((120, 820), "Memory pays off on the second failure, and grows with every incident.", font=font(40, bold=True), fill=INK)
    return img


def s_safety():
    img, d = base("Safe by default", "Built to be trusted with real systems")
    bullets(d, [
        "A human approves every fix; nothing changes without a decision.",
        "Backup before every change, health check after, automatic rollback.",
        "Below 70% confidence it says so and diagnoses fresh instead of guessing.",
        "Secrets and private hostnames are scrubbed before storage or any LLM call.",
        "Injected instructions in logs are stripped; logs are treated as data.",
        "Only allow-listed compose changes, only inside the project's folder.",
    ], size=38)
    return img


def s_close():
    img, d = base("Impact", "Every incident makes the next one faster")
    bullets(d, [
        "Time to resolve: first time vs repeat, measured live.",
        "Share of incidents resolved by a remembered fix.",
        "Suggestion accuracy: approved vs rejected by humans.",
        "Recurrences after a fix, so stale fixes surface.",
    ], size=40)
    d.text((120, 880), "DockerGPT Memory: log tools show what happened. We remember what fixed it.",
           font=font(42, bold=True), fill=ACCENT)
    return img


SCENES = [
    (s_title, "This is DockerGPT Memory: an agent that remembers every container fix, so no team has to solve the "
              "same failure twice. It is built with Docker, Kepler, a Gemma language model hosted on JarvisLabs, "
              "Temporal, and NudgeBee."),
    (s_problem, "Here is the problem. Teams hit the same container failures again and again: a port already in use, "
                "a container killed for running out of memory, a missing environment variable, a service that starts "
                "before its database. Each time, someone searches the logs, asks in chat, tries a few fixes, and moves on. "
                "The fix ends up in one person's head, or a buried chat thread. When that person leaves, the work starts again."),
    (s_why, "Existing tools don't solve this. Log tools show what happened, but not what fixed it last time. "
            "General chatbots give generic answers, with no knowledge of your stack. And the hard part is that a repeat "
            "failure rarely looks the same. These two apps fail for exactly the same reason, a missing database password, "
            "but their error messages share no words at all. A keyword search would never connect them."),
    (s_idea, "Our idea is simple. Every resolved failure becomes a lesson, stored with six parts: the symptom, the context, "
             "the evidence, the root cause, the fix, and the outcome. When a new container fails, the agent checks these "
             "lessons first, and explains why one matches."),
    (s_flow, "It works in eight steps. A watcher detects the failure from Docker events. Kepler, a local SRE agent, "
             "investigates. The agent retrieves similar lessons from memory. Gemma, running on JarvisLabs, decides "
             "whether a past case has the same root cause, states its confidence, and adapts the old fix. Then a human "
             "approves. The agent backs up and applies the fix, verifies the container stays healthy, and stores the "
             "lesson. If the health check fails, it rolls back automatically, and remembers that fix as one to avoid."),
    (s_arch, "Here is the architecture. A container crashes on the Docker host. The watcher reports it to the API, which "
             "starts one Temporal workflow for the incident. That workflow drives everything else: Kepler's diagnosis, "
             "memory search in Qdrant, reasoning by Gemma on JarvisLabs, the human approval from the dashboard, and the "
             "fix itself. Postgres, Qdrant and the Temporal server run on NudgeBee's own infrastructure."),
    (s_tech, "Every technology has a real job. Temporal makes the process durable: it survives crashes, waits for "
             "approval, rolls back, and keeps an audit trail. JarvisLabs hosts our Gemma model privately. NudgeBee is "
             "the platform underneath, and its past root cause reports can seed memory on day one. Kepler gives an "
             "independent diagnosis and also remembers every fix. Docker is both the trigger and the place the fix is applied."),
    (s_demo, "In the demo, the same failure is solved twice. First, a Python app is deployed without its database "
             "password. Memory has nothing that matches, so the agent says so and diagnoses it from scratch. A human "
             "approves, and the fix is applied, verified and stored. Then a Node app fails for the same reason, with "
             "completely different error text. This time the agent finds the earlier lesson, explains why it matches, "
             "adapts the fix to this app's variable name, and resolves it much faster."),
    (s_safety, "It is safe by default. A human approves every fix. Every change is backed up, verified, and rolled back "
               "if it fails. Below seventy percent confidence, it diagnoses fresh instead of guessing. Secrets are "
               "scrubbed before anything is stored or sent to the model, and instructions hidden in logs are ignored."),
    (s_close, "We measure the impact live: time to resolve the first time versus a repeat, the share of incidents "
              "resolved from memory, how often humans accept the suggestions, and how often failures come back. "
              "Log tools show what happened. DockerGPT Memory remembers what fixed it."),
]


def tts(texts: list[str]) -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    paths = []
    lines = ["Add-Type -AssemblyName System.Speech", "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
             f"$s.SelectVoice('{VOICE}')", "$s.Rate = 0"]
    for i, text in enumerate(texts):
        txt = WORK / f"n{i:02d}.txt"
        wav = WORK / f"n{i:02d}.wav"
        txt.write_text(text, encoding="utf-8")
        lines += [f"$s.SetOutputToWaveFile('{wav}')", f"$s.Speak([IO.File]::ReadAllText('{txt}'))"]
        paths.append(wav)
    lines.append("$s.Dispose()")
    ps1 = WORK / "tts.ps1"
    ps1.write_text("\n".join(lines), encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)], check=True)
    return paths


def main() -> None:
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    WORK.mkdir(parents=True, exist_ok=True)
    wavs = tts([n for _, n in SCENES])
    segments = []
    for i, ((render, _), wav) in enumerate(zip(SCENES, wavs)):
        png = WORK / f"s{i:02d}.png"
        render().save(png)
        with wave.open(str(wav)) as w:
            dur = w.getnframes() / w.getframerate() + 1.0
        seg = WORK / f"seg{i:02d}.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-loop", "1", "-i", str(png), "-i", str(wav),
                        "-af", "apad", "-t", f"{dur:.2f}", "-r", "30", "-c:v", "libx264", "-tune", "stillimage",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", str(seg)], check=True)
        segments.append(seg)
        print(f"scene {i + 1}/{len(SCENES)}: {dur:.1f}s")
    lst = WORK / "list.txt"
    lst.write_text("\n".join(f"file '{s.as_posix()}'" for s in segments), encoding="utf-8")
    final = OUT / "dockergpt-memory-explainer.mp4"
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                   check=True)
    print("wrote", final)


if __name__ == "__main__":
    main()
