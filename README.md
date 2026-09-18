# Clip Desk — local web app

A small local website that wraps the yt-dlp / ffmpeg commands you've already been using:
paste a link, pick an action, click Run, download the result. Everything runs on your
own PC — nothing is uploaded anywhere.

## 1. One-time setup

You already have `yt-dlp.exe` and `ffmpeg.exe` working in Git Bash — this reuses them.

**Install Python** (if you don't have it): https://www.python.org/downloads/ — during
install, tick "Add Python to PATH."

**Install Flask** (the tiny web server library). In Git Bash, from this folder:
```bash
pip install -r requirements.txt --break-system-packages
```
(if that flag errors on your system, just drop it: `pip install -r requirements.txt`)

## 2. Tell it where yt-dlp / ffmpeg live

If `yt-dlp.exe` and `ffmpeg.exe` are **not** on your system PATH, set two environment
variables before starting the app (adjust the paths to wherever your files actually are):

```bash
export YTDLP_PATH="/c/Users/CLIFFORD/Downloads/Here/yt-dlp.exe"
export FFMPEG_PATH="/c/Users/CLIFFORD/Downloads/Here/ffmpeg.exe"
```

Easiest option: just copy `app.py` (and the whole `media_toolkit` folder) into the same
folder as your two `.exe` files. Then the defaults (`yt-dlp`, `ffmpeg`) will find them
automatically as long as you launch the app from that folder.

## 2b. (Optional) Choose where finished files are saved

By default, finished files land in `media_toolkit/downloads`. If you'd rather they go
straight to your Desktop, or any other folder, set the `OUTPUT_DIR` environment variable
before starting the app:

```bash
export OUTPUT_DIR="/c/Users/CLIFFORD/Desktop/ClipDeskOutputs"
```

The folder will be created automatically if it doesn't already exist. This only needs
to be set once per terminal session — if you close and reopen Git Bash/VS Code's
terminal, you'll need to run that line again before `python app.py` (or add it to a
`.bashrc` file if you want it permanent — happy to walk through that if you want it).

If two finished files end up with the same name, the app won't overwrite the older one —
it'll automatically save the new one as `name (2).ext` instead.

## 3. Run it

```bash
python app.py
```

You'll see something like:
```
Using yt-dlp at: yt-dlp
Using ffmpeg at: ffmpeg
 * Running on http://127.0.0.1:5000
```

Open **http://localhost:5000** in your browser. Leave the Git Bash window open —
closing it stops the server.

## 4. Using it

1. Pick an action: Audio only, Full video, Trim a section, GIF, Telegram video sticker,
   or Trim an MP3.
2. Paste the link (YouTube, Shorts, TikTok, Twitter/X, Instagram, Reddit, Tenor —
   anything yt-dlp supports).
3. If the action needs it, fill in **Start** (e.g. `00:00:02.45`) and **Duration**
   (e.g. `1.29`) in seconds.
4. Give it a file name.
5. Click **Run**. When it finishes, click **Download**.

Finished files are also saved on disk under `media_toolkit/downloads/<job-id>/`, in case
you want to grab them directly without downloading through the browser.

## Notes

- **GIF / sticker quality**: the app uses the same two-step palette method from your
  cheat sheet, so output should look as clean as what you were getting manually.
- **Stickers**: video stickers are capped at 512×512, transparent background, VP9/WEBM,
  no audio — matches Telegram's requirements. Check the file size shown after processing
  stays under 256KB; if it's too big, lower quality slightly by trimming a shorter
  duration (the app doesn't expose a bitrate slider yet — say the word if you want one
  added).
- **Netflix / DRM-protected services**: still out of scope, same as before.
- **Speed**: trim-MP3 only downloads audio, GIF/sticker only download video, and GIFs
  build their palette in one ffmpeg pass instead of two — none of that affects output
  quality, it just skips work whose result would get thrown away anyway. The full
  video itself is always downloaded (see the next point), so overall time is mostly
  bounded by your connection speed and the source video's length.
- **A trim/GIF/sticker job takes a while for a long video**: this is expected now.
  Every trim/GIF/sticker/MP3-trim job downloads the entire source video first, then
  slices it locally with ffmpeg — the app used to also try a fast partial download
  (only fetching the needed slice) before falling back to this, but that path got
  removed after testing showed it wasn't reliable enough across sites: CDN
  range-request throttling and stalls, MP4s with their index at the end of the file,
  WebM streams that can only be read sequentially, and — worst — clips getting cut
  mid-GOP on some platforms with no leading keyframe (audio starts immediately, video
  stays blank/frozen until the next real keyframe arrives). A full, plain, sequential
  download is what every site treats as ordinary playback, so nothing throttles or
  mishandles it, and it's always frame-accurate once local. The download has its own
  longer timeout (`CLIP_DOWNLOAD_TIMEOUT`, 1 hour by default) since it can legitimately
  take a while on a slow connection for a long video. AV1 formats are avoided
  (`libaom-av1`, the software encoder, is 10-50x slower than VP9/H.264 for the same
  re-encode) and resolution is capped at 1080p (`CLIP_MAX_HEIGHT`) — neither of those
  needs 4K/AV1 source for a short social clip.
- **Live progress**: the terminal running `python app.py` now prints every yt-dlp/ffmpeg
  command and its output live, as it happens — useful for seeing exactly which format got
  picked and whether a job is progressing or stuck.
- **"HTTP Error 403: Forbidden"**: this is YouTube, not the app — it periodically breaks
  one of yt-dlp's internal "player clients" (this has happened a few times in 2026), which
  makes every download fail until yt-dlp is updated. First try:
  ```bash
  pip install -U yt-dlp
  ```
  If it still 403s right after updating, YouTube likely broke a *different* client. You can
  set the `YTDLP_EXTRACTOR_ARGS` environment variable to skip whichever one is currently
  broken, e.g.:
  ```bash
  export YTDLP_EXTRACTOR_ARGS="youtube:player_client=default,-web_creator"
  ```
  (check yt-dlp's GitHub issues for the current client name if unsure). Only set this
  while a specific client is actually broken — see the note below on why leaving it set
  permanently can cause a different failure.
- **"HTTP 429 Too Many Requests" / "Sign in to confirm you're not a bot"**: YouTube is
  rate-limiting or bot-checking your connection — common after a burst of requests in a
  short time. Set your browser's name before starting the app to have yt-dlp
  authenticate using your existing YouTube login session instead of looking like an
  anonymous script:
  ```bash
  export YTDLP_COOKIES_FROM_BROWSER="chrome"
  ```
  (also accepts `firefox`, `edge`, `brave`, etc.) Note this uses a real logged-in
  session from that browser, not an anonymous setting — only set it if that's actually
  what you want. If you see "Could not copy Chrome cookie database" (yt-dlp issue
  #7271), Chrome is still running somewhere — fully quit it (check Task Manager for a
  lingering `chrome.exe`, not just closed windows) and retry, or switch to
  `firefox`/`edge`, or export a cookies.txt file once (e.g. via a browser extension
  like "Get cookies.txt LOCALLY") and set `YTDLP_COOKIES_FILE` to its path instead —
  a static file has no lock to conflict with.
- **"The page needs to be reloaded" / videos silently losing formats**: YouTube signs
  video URLs with a constantly-changing JS challenge. yt-dlp needs its own official
  solver script (`yt-dlp-ejs`, hosted on yt-dlp's GitHub) plus a JS runtime (Deno
  recommended — https://deno.land) to handle this — yt-dlp's own docs call it required
  for full YouTube support. This app allows fetching that solver by default
  (`YTDLP_REMOTE_COMPONENTS=ejs:github`); if you'd rather it never auto-fetch anything,
  set `YTDLP_REMOTE_COMPONENTS=` (empty) to disable it — note that turning it off can
  bring back exactly this failure.
- **"No video formats found!" repeated many times, "Downloading item N of M"**: the
  pasted link was a carousel/album post (Instagram multi-photo posts are the usual
  culprit) — yt-dlp was trying to download every slide as if it were a playlist, and
  photo-only slides correctly have no video to extract. The app now passes
  `--no-playlist` to yt-dlp so it only grabs the single item the link points to. If
  that single item still isn't the video you wanted (e.g. the video is slide 5 of 8,
  not the first slide), try getting the direct link to that specific slide instead of
  the post as a whole.
- **"Only images are available for download" / "Requested format is not available"**:
  this means yt-dlp couldn't decode YouTube's signed video URLs and fell back to
  thumbnails only. It needs a JavaScript runtime to do that — install Deno
  (https://deno.land; on Windows: `winget install DenoLand.Deno`), then restart the app.
  yt-dlp detects it automatically, no config needed. This can also happen if
  `YTDLP_EXTRACTOR_ARGS` is set to exclude Android-family clients (they're the ones that
  *don't* need a JS runtime) — if you set that variable to work around a 403 earlier,
  unset it once yt-dlp has caught up with a proper fix.

