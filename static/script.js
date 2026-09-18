const chips = document.querySelectorAll(".chip");
const timeFields = document.getElementById("time-fields");
const runBtn = document.getElementById("run");
const runLabel = runBtn.querySelector(".run-label");
const runEmoji = runBtn.querySelector(".run-emoji");
const spinner = runBtn.querySelector(".spinner");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const choiceBox = document.getElementById("choice-box");
const choiceText = document.getElementById("choice-text");
const choiceLowerBtn = document.getElementById("choice-lower");
const choiceShortenBtn = document.getElementById("choice-shorten");
const durationInput = document.getElementById("duration");

let currentAction = document.querySelector(".chip.active").dataset.action;
let currentEmoji = document.querySelector(".chip.active").dataset.emoji;

// ---------------------------------------------------------------------------
// Emoji burst — the one signature bit of flair, kept to this single moment
// ---------------------------------------------------------------------------
const BURST_SETS = {
  audio_only: ["🎧", "🎶", "✨"],
  full_video: ["🎬", "🍿", "✨"],
  trim_clip: ["✂️", "🎬", "🔥"],
  gif: ["🌀", "✨", "🔥"],
  sticker: ["🏷️", "✨", "🎉"],
  trim_mp3: ["🎵", "🎶", "✨"],
  success: ["🎉", "✨", "🔥", "🙌"],
};

function emojiBurst(originEl, emojis, count = 10) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const rect = originEl.getBoundingClientRect();
  const originX = rect.left + rect.width / 2;
  const originY = rect.top + rect.height / 2;

  for (let i = 0; i < count; i++) {
    const span = document.createElement("span");
    span.className = "burst-emoji";
    span.textContent = emojis[Math.floor(Math.random() * emojis.length)];
    const angle = (Math.random() * Math.PI) + Math.PI; // upward-ish spread
    const distance = 60 + Math.random() * 90;
    const tx = Math.cos(angle) * distance;
    const ty = Math.sin(angle) * distance;
    span.style.setProperty("--tx", `${tx}px`);
    span.style.setProperty("--ty", `${ty}px`);
    span.style.setProperty("--rot", `${(Math.random() - 0.5) * 240}deg`);
    span.style.setProperty("--dur", `${0.7 + Math.random() * 0.5}s`);
    span.style.left = `${originX}px`;
    span.style.top = `${originY}px`;
    document.body.appendChild(span);
    span.addEventListener("animationend", () => span.remove());
  }
}

// ---------------------------------------------------------------------------
// Action chip selection
// ---------------------------------------------------------------------------
chips.forEach((chip) => {
  chip.addEventListener("click", () => {
    chips.forEach((c) => c.classList.remove("active"));
    chip.classList.add("active");
    currentAction = chip.dataset.action;
    currentEmoji = chip.dataset.emoji;
    runEmoji.textContent = currentEmoji;
    timeFields.hidden = chip.dataset.needsTime !== "true";
    resultEl.hidden = true;
    statusEl.hidden = true;
    choiceBox.hidden = true;
    emojiBurst(chip, [chip.dataset.emoji, "✨"], 6);
  });
});
timeFields.hidden = document.querySelector(".chip.active").dataset.needsTime !== "true";
runEmoji.textContent = currentEmoji;

// ---------------------------------------------------------------------------
// Run flow
// ---------------------------------------------------------------------------
function setWorking(isWorking) {
  runBtn.disabled = isWorking;
  runLabel.textContent = isWorking ? "Cooking…" : "Let's go";
  runEmoji.hidden = isWorking;
  spinner.hidden = !isWorking;
}

function showStatus(message, kind) {
  statusEl.hidden = false;
  statusEl.textContent = message;
  statusEl.className = "status " + kind;
}

async function submit(extra = {}) {
  const url = document.getElementById("url").value.trim();
  const filename = document.getElementById("filename").value.trim() || "output";
  const start = document.getElementById("start").value.trim();
  const duration = document.getElementById("duration").value.trim();

  resultEl.hidden = true;
  statusEl.hidden = true;
  choiceBox.hidden = true;

  if (!url) {
    showStatus("Paste a link first, bestie 👀", "error");
    return;
  }

  setWorking(true);
  showStatus("Running yt-dlp / ffmpeg — might take a sec for longer stuff…", "working");

  try {
    const res = await fetch("/api/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: currentAction, url, filename, start, duration, ...extra }),
    });
    const data = await res.json();

    if (data.needs_choice) {
      statusEl.hidden = true;
      choiceBox.hidden = false;
      choiceText.textContent = `😅 ${data.error} What do you want to do?`;
      return;
    }

    if (!data.ok) {
      showStatus(data.error || "Something went wrong.", "error");
      return;
    }

    statusEl.hidden = true;
    resultEl.hidden = false;
    document.getElementById("result-name").textContent = data.filename;
    const metaParts = [`${data.size_kb} KB`];
    if (data.note) metaParts.push(data.note);
    document.getElementById("result-meta").textContent = metaParts.join(" — ");
    document.getElementById("result-path").textContent = data.path;
    emojiBurst(runBtn, BURST_SETS.success, 14);
  } catch (err) {
    showStatus("Couldn't reach the local server. Is app.py still running?", "error");
  } finally {
    setWorking(false);
  }
}

runBtn.addEventListener("click", () => {
  emojiBurst(runBtn, BURST_SETS[currentAction] || BURST_SETS.success, 8);
  submit();
});

choiceLowerBtn.addEventListener("click", () => {
  choiceBox.hidden = true;
  submit({ auto_reduce: true });
});

choiceShortenBtn.addEventListener("click", () => {
  choiceBox.hidden = true;
  showStatus("Got it — shorten the Duration below (try under 1.5s) then hit Let's go again.", "working");
  durationInput.focus();
  durationInput.style.boxShadow = "4px 4px 0 var(--pink)";
});
