"""
Media Toolkit — local web UI for yt-dlp + ffmpeg
Run with:  python app.py
Then open: http://localhost:5000
"""

import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_from_directory, abort

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
# Point these at your yt-dlp.exe / ffmpeg.exe if they aren't on PATH
YTDLP_PATH = os.environ.get("YTDLP_PATH", "yt-dlp")
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")

# YouTube periodically breaks one specific "player client" that yt-dlp uses
# under the hood (android_sdkless in Jan 2026, android_vr in Aug 2026 — both
# caused every download to fail with "HTTP Error 403: Forbidden"). The real
# fix is to update yt-dlp (pip install -U yt-dlp) — that's what actually
# resolves it. This env var is only a manual escape hatch for when a client
# breaks and yt-dlp hasn't shipped a fix yet. Leave it unset by default:
# forcing out a client permanently (e.g. excluding android_vr) pushes yt-dlp
# onto web-based clients that need a JS runtime (deno) to decode signed
# URLs, and without one installed, downloads silently degrade to
# thumbnail-only images. Only set this if you're actively working around a
# known-broken client, e.g.:
#   YTDLP_EXTRACTOR_ARGS=youtube:player_client=default,-web_creator
YTDLP_EXTRACTOR_ARGS = os.environ.get("YTDLP_EXTRACTOR_ARGS", "")
YTDLP_EXTRA_ARGS = shlex.split(f"--extractor-args {shlex.quote(YTDLP_EXTRACTOR_ARGS)}") if YTDLP_EXTRACTOR_ARGS else []

# If YouTube starts responding with "429 Too Many Requests" or "Sign in to
# confirm you're not a bot", it's rate-limiting/bot-checking your IP —
# common after a lot of requests in a short time. yt-dlp's own recommended
# fix is to authenticate using a real browser session instead of looking
# like an anonymous script. Set this to your browser's name (chrome,
# firefox, edge, brave, etc.) to have yt-dlp reuse that browser's existing
# YouTube login cookies. Leave unset by default — this uses a real logged-in
# session, not an anonymous setting, so it's opt-in only:
#   YTDLP_COOKIES_FROM_BROWSER=chrome
YTDLP_COOKIES_FROM_BROWSER = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "")

# Alternative to the above: a cookies.txt file exported once from your
# browser (e.g. via the "Get cookies.txt LOCALLY" extension), instead of
# live-reading the browser's cookie database. Useful when
# --cookies-from-browser fails to copy a locked/in-use browser database
# (common with Chrome while it's still running) — a static file has no
# such lock. Takes priority over YTDLP_COOKIES_FROM_BROWSER if both are set:
#   YTDLP_COOKIES_FILE=/c/Users/you/Downloads/cookies.txt
YTDLP_COOKIES_FILE = os.environ.get("YTDLP_COOKIES_FILE", "")

if YTDLP_COOKIES_FILE:
    YTDLP_COOKIE_ARGS = ["--cookies", YTDLP_COOKIES_FILE]
elif YTDLP_COOKIES_FROM_BROWSER:
    YTDLP_COOKIE_ARGS = ["--cookies-from-browser", YTDLP_COOKIES_FROM_BROWSER]
else:
    YTDLP_COOKIE_ARGS = []

# YouTube signs its video URLs with a JS-computed signature/"n" challenge
# that changes constantly; yt-dlp's own official solver script for this
# (yt-dlp-ejs, hosted on yt-dlp's own GitHub) has to be explicitly allowed
# to fetch, since yt-dlp doesn't fetch remote code by default. Without it,
# some videos fail with "The page needs to be reloaded" or silently lose
# formats. yt-dlp's own docs now call this "required for full YouTube
# support," so it's on by default here — but it does mean yt-dlp
# automatically downloads and runs that solver script (from yt-dlp's own
# repo, not a third party) the first time it's needed. Set this to an empty
# string to disable if you'd rather it never fetch anything automatically:
#   YTDLP_REMOTE_COMPONENTS=
YTDLP_REMOTE_COMPONENTS = os.environ.get("YTDLP_REMOTE_COMPONENTS", "ejs:github")
YTDLP_REMOTE_ARGS = ["--remote-components", YTDLP_REMOTE_COMPONENTS] if YTDLP_REMOTE_COMPONENTS else []

# Downloaded source video is capped at this height. Re-encoding even a few
# seconds of 4K/2K for a cut is real decode+encode work, and nothing this
# app outputs (social clips, Telegram stickers, GIFs) benefits from 4K
# source. Override with CLIP_MAX_HEIGHT if you ever want higher-res output.
MAX_HEIGHT = os.environ.get("CLIP_MAX_HEIGHT", "1080")

# Downloads can legitimately take a while for long videos on a slow
# connection — longer than the default job timeout used for local ffmpeg
# processing steps.
DOWNLOAD_TIMEOUT = int(os.environ.get("CLIP_DOWNLOAD_TIMEOUT", "3600"))

# Where FINISHED files land. Defaults to media_toolkit/downloads if not set.
# Set the OUTPUT_DIR environment variable (see README) to send results
# straight to e.g. your Desktop or a specific project folder instead.
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", BASE_DIR / "downloads")).expanduser()
SCRATCH_DIR = BASE_DIR / ".scratch"  # temporary working files only — safe to ignore/delete
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SCRATCH_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB upload cap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_name(name: str) -> str:
    """Strip a user-provided filename down to something safe for the shell/filesystem."""
    name = re.sub(r"[^A-Za-z0-9_\-]", "_", name).strip("_")
    return name or "output"


def job_dir() -> Path:
    """Each request gets its own scratch folder so concurrent jobs never collide."""
    d = SCRATCH_DIR / uuid.uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=True)
    return d


def deliver(out_path: Path) -> Path:
    """Move the finished file out of scratch space and into OUTPUT_DIR.
    If a file with that name already exists there, add a (2), (3)... suffix
    instead of overwriting it."""
    target = OUTPUT_DIR / out_path.name
    if target.exists():
        stem, suffix = out_path.stem, out_path.suffix
        n = 2
        while (OUTPUT_DIR / f"{stem} ({n}){suffix}").exists():
            n += 1
        target = OUTPUT_DIR / f"{stem} ({n}){suffix}"
    shutil.move(str(out_path), str(target))
    return target


def _kill_process_tree(proc: subprocess.Popen):
    """Kill a process AND its children. yt-dlp spawns ffmpeg as a child
    process (e.g. to merge separate video/audio streams); plain proc.kill()
    only terminates yt-dlp itself, and Windows doesn't clean up orphaned
    children automatically — a timed-out job could otherwise leave ffmpeg
    running in the background indefinitely."""
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        proc.kill()


def run(cmd, cwd, timeout=600):
    # Print the exact command, and stream its output live to this terminal as
    # it runs — previously all output was captured silently until the whole
    # thing finished (or timed out), so a slow job looked identical to a
    # frozen one. Now you'll see yt-dlp's format selection and ffmpeg's
    # progress in real time, same as running it by hand.
    print("\n$ " + " ".join(shlex.quote(str(c)) for c in cmd), flush=True)
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    # `for line in proc.stdout` blocks waiting for the NEXT line — it has no
    # timeout of its own, so if the process goes quiet mid-run (e.g. a
    # stalled network connection that never errors, just stops delivering
    # data) the loop just waits forever and proc.wait(timeout=...) never
    # even gets a turn to enforce anything. A background timer kills the
    # process on a wall-clock deadline regardless of whether output is
    # currently flowing.
    timed_out = threading.Event()
    timer = threading.Timer(timeout, lambda: (timed_out.set(), _kill_process_tree(proc)))
    timer.start()
    lines = []
    try:
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(cmd, timeout)
    log = "".join(lines)
    if proc.returncode != 0:
        if "needs to be reloaded" in log or "n challenge" in log.lower():
            raise RuntimeError(
                "YouTube's JS signature challenge couldn't be solved. Make sure a JS "
                "runtime (Deno recommended — https://deno.land) is installed, and that "
                "remote components are allowed so yt-dlp can fetch its own solver "
                "script: this app enables that by default "
                "(YTDLP_REMOTE_COMPONENTS=ejs:github) unless you've unset it.\n\n"
                + log[-1500:]
            )
        if "Sign in to confirm you" in log or "429" in log and "Too Many Requests" in log:
            raise RuntimeError(
                "YouTube is rate-limiting/bot-checking this connection (HTTP 429 or "
                "a sign-in prompt) — common after a lot of requests in a short time. "
                "Set the YTDLP_COOKIES_FROM_BROWSER environment variable to your "
                "browser's name (e.g. chrome, firefox, edge) before starting the app "
                "to authenticate using your existing YouTube login instead of looking "
                "like an anonymous script.\n\n" + log[-1500:]
            )
        if "No video formats found" in log:
            raise RuntimeError(
                "This link has no video to extract — likely a photo-only post, or a "
                "carousel/album where the video isn't the first slide. If it's a "
                "carousel, try getting the direct link to that specific slide/video "
                "rather than the post as a whole.\n\n" + log[-1500:]
            )
        if "Only images are available" in log or "Requested format is not available" in log:
            raise RuntimeError(
                "YouTube only offered thumbnail images, not real video/audio formats. "
                "This almost always means yt-dlp needs a JavaScript runtime to decode "
                "signed URLs. Install Deno (https://deno.land — on Windows: "
                "winget install DenoLand.Deno), then restart the app; yt-dlp finds it "
                "automatically.\n\n" + log[-2000:]
            )
        if "403" in log and "yt-dlp" in cmd[0]:
            raise RuntimeError(
                "YouTube returned 403 Forbidden. This usually means yt-dlp is out of "
                "date (run: pip install -U yt-dlp) or the player client it picked has "
                "been temporarily broken by YouTube — see YTDLP_EXTRACTOR_ARGS in the "
                f"README for a workaround.\n\n{log[-2000:]}"
            )
        raise RuntimeError(log[-4000:] or "Command failed with no output.")
    return log


def find_downloaded_file(folder: Path, stem: str) -> Path:
    """yt-dlp sometimes changes the extension during merging (e.g. .mp4 -> .mp4.webm)."""
    matches = sorted(folder.glob(f"{stem}*"))
    if not matches:
        raise RuntimeError("yt-dlp did not produce an output file.")
    return matches[0]


def parse_time(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        raise RuntimeError(f"{field} is required for this action.")
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?|[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?(\.[0-9]+)?", value):
        raise RuntimeError(f"{field} looks invalid: '{value}'. Use seconds (2.45) or HH:MM:SS.")
    return value


def to_seconds(value: str) -> float:
    """Convert a validated time string ('2.45' or 'HH:MM:SS(.ms)') to float seconds."""
    parts = [float(p) for p in value.split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def download_source(folder: Path, url: str, stem: str = "src", format_selector: str = None) -> Path:
    """Download the entire source, then the caller slices it locally with
    ffmpeg (see build_seek_args). This app used to also try a fast
    partial/section download first (only fetching the needed slice) —
    that's been removed: it was fast when it worked, but proved unreliable
    across sites — CDN range-request throttling, MP4s with their index at
    the end of the file, WebM streams that can only be read sequentially,
    and (worst) clips getting cut mid-GOP on some platforms with no leading
    keyframe (audio starts immediately, video stays blank/frozen until the
    next real keyframe). A plain, full, sequential download is what every
    site treats as ordinary playback — nothing to throttle or mishandle —
    and once the file is local, ffmpeg's seek is instant and reliable
    because it's reading a real index off disk instead of negotiating byte
    ranges with a CDN.

    Format selection has NO codec preference — just the resolution cap
    (MAX_HEIGHT). Earlier versions of this function preferred H.264/MP4
    (avc1) specifically, because that container supports efficient
    byte-range seeking, which mattered for the old partial-download path.
    That path is gone, so there's no seeking left to protect — and on a lot
    of modern YouTube uploads (this includes many Shorts), H.264 tops out
    at 480–720p while 1080p+ is VP9/AV1-only. Preferring avc1 in that case
    means silently downloading a much lower-resolution version even when a
    much better one exists, which is a real quality loss for no remaining
    benefit. Letting yt-dlp pick its own best-quality match within the
    height cap, codec-agnostic, is strictly better now.
    """
    h = MAX_HEIGHT
    if format_selector == "bestaudio":
        fmt = "bestaudio"
    elif format_selector == "bestvideo":
        fmt = f"bestvideo[height<={h}]/bestvideo"
    else:
        fmt = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
    cmd = [YTDLP_PATH, url, "--no-playlist", "-f", fmt, *YTDLP_EXTRA_ARGS, *YTDLP_COOKIE_ARGS, *YTDLP_REMOTE_ARGS, "-o", f"{stem}.%(ext)s"]
    if FFMPEG_PATH and FFMPEG_PATH != "ffmpeg":
        cmd += ["--ffmpeg-location", FFMPEG_PATH]
    run(cmd, cwd=folder, timeout=DOWNLOAD_TIMEOUT)
    return find_downloaded_file(folder, stem)


def build_seek_args(real_start: float):
    """Returns (pre_input_args, output_side_ss) for trimming with ffmpeg.

    Source is always the whole downloaded video (see download_source), so
    this always needs a real seek. Uses the standard fast+accurate ffmpeg
    seeking pattern instead of choosing between "slow but exact" and "fast
    but keyframe-snapped": a big, approximate seek BEFORE -i (near-instant,
    lands close to the target) followed by a small, precise seek AFTER -i
    (decodes just the short remaining gap to hit the exact frame).
    """
    margin = 15.0  # comfortably more than a typical keyframe interval
    input_ss = max(0.0, real_start - margin)
    output_ss = real_start - input_ss
    return ["-ss", f"{input_ss:.3f}"], f"{output_ss:.3f}"


# ---------------------------------------------------------------------------
# Action implementations — each returns the path to the final file
# ---------------------------------------------------------------------------
def action_audio_only(folder, url, filename, **_):
    stem = "src"
    run([YTDLP_PATH, url, "--no-playlist", "--extract-audio", "--audio-format", "mp3", *YTDLP_EXTRA_ARGS, *YTDLP_COOKIE_ARGS, *YTDLP_REMOTE_ARGS, "-o", f"{stem}.%(ext)s"], cwd=folder)
    src = find_downloaded_file(folder, stem)
    out = folder / f"{filename}.mp3"
    src.rename(out)
    return out


def action_full_video(folder, url, filename, **_):
    stem = "src"
    run([YTDLP_PATH, url, "--no-playlist", *YTDLP_EXTRA_ARGS, *YTDLP_COOKIE_ARGS, *YTDLP_REMOTE_ARGS, "-o", f"{stem}.%(ext)s"], cwd=folder)
    src = find_downloaded_file(folder, stem)
    out = folder / f"{filename}{src.suffix}"
    src.rename(out)
    return out


def action_trim_clip(folder, url, filename, start, duration, **_):
    src = download_source(folder, url)
    out = folder / f"{filename}.mp4"
    pre, post = build_seek_args(to_seconds(start))
    # Always re-encode — this guarantees a real keyframe at the exact cut
    # point regardless of the source's own GOP structure. "veryfast" only
    # trades compression efficiency for speed, not visual quality or cut
    # accuracy.
    run([FFMPEG_PATH, *pre, "-i", str(src), "-ss", post, "-t", duration,
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-y", str(out)], cwd=folder)
    return out


def action_gif(folder, url, filename, start, duration, **_):
    # GIFs have no audio, so skip downloading/muxing an audio track at all.
    src = download_source(folder, url, format_selector="bestvideo")
    out = folder / f"{filename}.gif"
    pre, post = build_seek_args(to_seconds(start))
    # Palette-based GIF in a single ffmpeg pass: split the decoded frames into
    # two branches (one builds the palette, one applies it) instead of
    # decoding the source twice in two separate ffmpeg calls. Same algorithm,
    # same output, roughly half the work.
    run([FFMPEG_PATH, *pre, "-i", str(src), "-ss", post, "-t", duration,
         "-filter_complex",
         "[0:v] fps=15,scale=480:-1:flags=lanczos,split [a][b];"
         "[a] palettegen [p];"
         "[b][p] paletteuse",
         "-y", str(out)], cwd=folder)
    return out


STICKER_MAX_BYTES = 256 * 1024

# Each step is (bitrate, crf). Tried in order from best quality down to smallest,
# stopping at the first one that lands under Telegram's 256KB sticker limit.
STICKER_QUALITY_STEPS = [
    ("600k", 24),
    ("450k", 26),
    ("350k", 28),
    ("250k", 30),
    ("180k", 32),
    ("120k", 34),
    ("80k", 36),
]


class NeedsChoice(Exception):
    """Raised when the sticker is too big at best quality and we want the
    person to decide how to fix it, instead of silently downgrading."""
    def __init__(self, size_kb):
        self.size_kb = size_kb
        super().__init__(f"That came out to {size_kb:.1f} KB — over Telegram's 256KB sticker limit.")


def _encode_sticker_step(src, out, vf, pre, post, duration, bitrate, crf):
    run([FFMPEG_PATH, *pre, "-i", str(src), "-ss", post, "-t", duration,
         "-vf", vf, "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-metadata:s:v:0", "alpha_mode=1", "-auto-alt-ref", "0",
         "-row-mt", "1", "-b:v", bitrate, "-crf", str(crf), "-an", "-y", str(out)], cwd=out.parent)
    return out.stat().st_size


def action_sticker(folder, url, filename, start, duration, auto_reduce=False, **_):
    # Stickers are silent (-an below), so skip downloading/muxing audio.
    src = download_source(folder, url, format_selector="bestvideo")
    out = folder / f"{filename}.webm"
    vf = ("scale=512:512:force_original_aspect_ratio=decrease,"
          "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000,fps=30,format=yuva420p")
    pre, post = build_seek_args(to_seconds(start))

    # Always try best quality first.
    bitrate, crf = STICKER_QUALITY_STEPS[0]
    size = _encode_sticker_step(src, out, vf, pre, post, duration, bitrate, crf)
    if size <= STICKER_MAX_BYTES:
        return out, None

    if not auto_reduce:
        # Don't silently downgrade — let the person choose how to fix it.
        raise NeedsChoice(size / 1024)

    # They chose "lower quality automatically" — keep stepping down.
    for i, (bitrate, crf) in enumerate(STICKER_QUALITY_STEPS[1:], start=1):
        size = _encode_sticker_step(src, out, vf, pre, post, duration, bitrate, crf)
        if size <= STICKER_MAX_BYTES:
            note = (f"Quality was automatically reduced to fit Telegram's 256KB sticker "
                     f"limit ({size / 1024:.1f} KB, step {i + 1} of {len(STICKER_QUALITY_STEPS)}).")
            return out, note

    raise RuntimeError(
        f"Even at the lowest quality setting this clip comes out to {size / 1024:.1f} KB — "
        f"still over Telegram's 256KB sticker limit. Try a shorter Duration (e.g. under 1.5 "
        f"seconds) and run it again."
    )


def action_trim_mp3(folder, url, filename, start, duration, **_):
    # Only the audio track is ever used, so don't download video at all.
    src = download_source(folder, url, format_selector="bestaudio")
    out = folder / f"{filename}.mp3"
    pre, post = build_seek_args(to_seconds(start))
    run([FFMPEG_PATH, *pre, "-i", str(src), "-ss", post, "-t", duration,
         "-vn", "-acodec", "libmp3lame", "-y", str(out)], cwd=folder)
    return out


ACTIONS = {
    "audio_only":  {"label": "Audio only",            "needs_time": False, "fn": action_audio_only},
    "full_video":  {"label": "Full video",             "needs_time": False, "fn": action_full_video},
    "trim_clip":   {"label": "Trim a section (with audio)", "needs_time": True, "fn": action_trim_clip},
    "gif":         {"label": "GIF",                    "needs_time": True,  "fn": action_gif},
    "sticker":     {"label": "Telegram video sticker",  "needs_time": True,  "fn": action_sticker},
    "trim_mp3":    {"label": "Trim an MP3",             "needs_time": True,  "fn": action_trim_mp3},
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", actions=ACTIONS)


@app.route("/api/download/<path:filename>")
def download_file(filename):
    # Serves a finished file back over HTTP. This matters once the app runs
    # somewhere other than your own PC (e.g. Render): the browser can't just
    # read a path on the server's disk the way it could read a path on your
    # own machine, so the finished file has to be sent back as an actual HTTP
    # response. send_from_directory also blocks path-traversal attempts
    # (e.g. "../../etc/passwd") on its own.
    target = (OUTPUT_DIR / filename).resolve()
    if OUTPUT_DIR.resolve() not in target.parents or not target.is_file():
        abort(404)
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


@app.route("/api/process", methods=["POST"])
def process():
    data = request.get_json(force=True)
    action_key = data.get("action")
    url = (data.get("url") or "").strip()
    filename = safe_name(data.get("filename") or "output")
    start = data.get("start", "")
    duration = data.get("duration", "")

    if action_key not in ACTIONS:
        return jsonify({"ok": False, "error": "Unknown action."}), 400
    if not url:
        return jsonify({"ok": False, "error": "Paste a link first."}), 400

    action = ACTIONS[action_key]
    auto_reduce = bool(data.get("auto_reduce", False))
    if action["needs_time"]:
        try:
            start = parse_time(start, "Start time")
            duration = parse_time(duration, "Duration")
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    folder = job_dir()
    try:
        result = action["fn"](folder, url, filename, start=start, duration=duration, auto_reduce=auto_reduce)
        out_path, note = result if isinstance(result, tuple) else (result, None)
        final_path = deliver(out_path)
    except NeedsChoice as e:
        return jsonify({
            "ok": False,
            "needs_choice": True,
            "size_kb": round(e.size_kb, 1),
            "error": str(e),
        }), 200
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Timed out — the source may be too long or unreachable."}), 500
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"Unexpected error: {e}"}), 500
    finally:
        shutil.rmtree(folder, ignore_errors=True)  # clean up scratch files either way

    size_kb = round(final_path.stat().st_size / 1024, 1)
    return jsonify({
        "ok": True,
        "filename": final_path.name,
        "path": str(final_path),
        "download_url": f"/api/download/{final_path.name}",
        "size_kb": size_kb,
        "note": note,
    })


if __name__ == "__main__":
    print(f"Using yt-dlp at: {YTDLP_PATH}")
    print(f"Using ffmpeg at: {FFMPEG_PATH}")
    print(f"Saving finished files to: {OUTPUT_DIR.resolve()}")
    # use_reloader=False: on some systems (OneDrive-synced folders, some antivirus
    # setups) Flask's file watcher picks up unrelated background file activity across
    # the whole Python install and keeps restarting the server mid-download. We don't
    # need live-reload for this tool, so it's simplest to just turn it off.
    app.run(debug=True, use_reloader=False, port=5000)
