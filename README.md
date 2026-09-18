# Clip Desk

A small web app that wraps yt-dlp / ffmpeg: paste a link, pick an action, click Run,
download the result. It can run either on your own PC or hosted on Render.

## Running locally

1. Install Python: https://www.python.org/downloads/ (tick "Add Python to PATH")
2. `pip install -r requirements.txt`
3. `python app.py`
4. Open http://localhost:5000

yt-dlp and ffmpeg are pulled in automatically (yt-dlp via `requirements.txt`; ffmpeg
you'll still need locally — same as before, point `FFMPEG_PATH` at your `ffmpeg.exe`
if it isn't on PATH).

## Deploying to Render (free tier)

See `DEPLOY.md` for the full click-by-click walkthrough. Short version: push this repo
to GitHub (excluding `.exe` files and `cookies.txt` — see `.gitignore`), create a Web
Service on Render pointed at it, and Render's Python environment already includes
ffmpeg — you only need `requirements.txt`.

**One real limitation of hosting this remotely: YouTube cookies.**

Locally, `YTDLP_COOKIES_FROM_BROWSER=chrome` works because yt-dlp reads cookies straight
out of your own Chrome install. On Render there is no browser — that variable won't do
anything there. Two consequences:

- If YouTube starts asking for a login/bot-check, the "authenticate as your browser"
  fix from the README's troubleshooting section isn't available the same way remotely.
- Even with a `cookies.txt` file uploaded (see below), the requests are now coming from
  Render's datacenter IP instead of your home IP — a login cookie issued to your IP,
  suddenly used from Oregon, can itself look suspicious to YouTube and get flagged or
  invalidated. It's a genuine trade-off, not a guaranteed fix.

**How to use cookies safely if you need them:**

1. Export a `cookies.txt` once from your browser (e.g. the "Get cookies.txt LOCALLY"
   extension), the same way the local troubleshooting section describes.
2. **Never commit this file to GitHub** — it's a real, working login session; anyone
   with it can act as you on that site. `.gitignore` already excludes it.
3. In the Render dashboard, go to your service → **Environment** → **Secret Files**,
   and add a secret file named `cookies.txt` with that content pasted in. Render stores
   it securely (not in your repo) and mounts it at `/etc/secrets/cookies.txt` at
   runtime.
4. Set an environment variable (regular, not secret file):
   ```
   YTDLP_COOKIES_FILE=/etc/secrets/cookies.txt
   ```
5. Cookies expire — when YouTube starts rejecting them again, just re-export and paste
   the updated content into the same Secret File.

If you don't need to touch anything gated behind a login (most public YouTube/TikTok/
Twitter links don't need this), skip cookies entirely and the app works the same as
locally.

## Notes

Everything from the original local-only notes still applies (GIF/sticker quality,
403 errors, the JS-challenge solver, etc.) — see the "Notes" section below, unchanged.
The one addition: on Render, finished files are served back through a real download
link in the browser (`/api/download/<file>`) rather than just being saved to a local
folder, since the app's disk isn't your disk anymore.

- **GIF / sticker quality**: the app uses the same two-step palette method from your
  cheat sheet, so output should look as clean as what you were getting manually.
- **Stickers**: video stickers are capped at 512×512, transparent background, VP9/WEBM,
  no audio — matches Telegram's requirements. Check the file size shown after processing
  stays under 256KB; if it's too big, lower quality slightly by trimming a shorter
  duration (the app doesn't expose a bitrate slider yet — say the word if you want one
  added).
- **Netflix / DRM-protected services**: still out of scope, same as before.
- **A trim/GIF/sticker job takes a while for a long video**: expected — every such job
  downloads the entire source video first, then slices it locally with ffmpeg, for
  reliability reasons across sites (see comments in `app.py` for the full explanation).
  The download has its own longer timeout (`CLIP_DOWNLOAD_TIMEOUT`, 1 hour by default).
- **"HTTP Error 403: Forbidden"**: YouTube periodically breaks one of yt-dlp's internal
  player clients. First try updating yt-dlp (redeploy on Render to pick up the latest
  version, since `requirements.txt` doesn't pin it). If it still 403s, set
  `YTDLP_EXTRACTOR_ARGS` to skip the broken client (check yt-dlp's GitHub issues for the
  current client name).
- **"HTTP 429 Too Many Requests" / "Sign in to confirm you're not a bot"**: see the
  cookies section above for the remote-hosting caveat.
- **"The page needs to be reloaded" / videos silently losing formats**: yt-dlp needs its
  official solver script (`yt-dlp-ejs`) plus a JS runtime. This app allows fetching the
  solver by default (`YTDLP_REMOTE_COMPONENTS=ejs:github`).
