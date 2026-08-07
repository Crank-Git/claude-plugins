#!/usr/bin/env node
// statusline — wraps claude-powerline and appends session trend sparklines.
//
// Claude Code hands the main status line only a snapshot (context_window.*,
// cost.*) with no history, so we persist our own per-session series and render
// two sparklines from it:
//
//   ◔ ▂▃▅▇▂▃▅  context-window LEVEL over time. Unlike a subagent's cumulative
//              token count this is non-monotonic — it drops hard at every
//              auto-compact, so the sawtooth shows compaction events.
//   $ ▁▁▃█▁▂▁  cost burn RATE (delta per sample). Cumulative cost only rises,
//              so the level would be a useless ramp; the delta shows bursts.
//
// Installed by `/statusline-spark:install`, which copies this file to
// $CLAUDE_CONFIG_DIR/statusline-spark/ and wires settings.json:
//   "statusLine": { "type": "command", "command": "node \"…/statusline.js\"", "refreshInterval": 10 }
//
// Environment overrides:
//   CLAUDE_CONFIG_DIR      config root (default ~/.claude)
//   CLAUDE_POWERLINE_BIN   explicit path to the claude-powerline executable

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const CONFIG_DIR = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
const CONFIG = path.join(CONFIG_DIR, "claude-powerline.json");
const HIST_DIR = path.join(CONFIG_DIR, "statusline-history");

const MIN_SAMPLE_GAP_MS = 15000; // don't record faster than this
const MAX_SAMPLES = 240; // ~1h of history at 15s
const SPARK_CELLS = 14; // buckets rendered
const PRUNE_AFTER_MS = 3 * 24 * 60 * 60 * 1000;
const TARGET_LINE = 1; // 0-based: powerline row 2 (the session row)

const SPARK = "▁▂▃▄▅▆▇█";
const C = {
  reset: "\x1b[0m",
  dim: "\x1b[2m",
  cyan: "\x1b[38;5;80m",
  violet: "\x1b[38;5;177m",
};

// Resolve claude-powerline without spawning probes — the status line re-runs
// every refreshInterval, so a failed spawn per candidate per tick is real cost.
// npm-global installs land in ~/.local/bin or a prefix on PATH depending on the
// node version manager, so check both rather than hardcoding one.
function resolvePowerline() {
  const explicit = process.env.CLAUDE_POWERLINE_BIN;
  if (explicit) return explicit;

  const seen = [path.join(os.homedir(), ".local", "bin", "claude-powerline")];
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir) seen.push(path.join(dir, "claude-powerline"));
  }
  for (const candidate of seen) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {}
  }
  return null;
}

function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

// Atomic write — several statusline invocations can overlap.
function writeJsonAtomic(file, data) {
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data));
  fs.renameSync(tmp, file);
}

function pruneOldHistory() {
  try {
    const now = Date.now();
    for (const f of fs.readdirSync(HIST_DIR)) {
      const p = path.join(HIST_DIR, f);
      try {
        if (now - fs.statSync(p).mtimeMs > PRUNE_AFTER_MS) fs.unlinkSync(p);
      } catch {}
    }
  } catch {}
}

// Append a sample for this session and return the full retained series.
function recordSample(sessionId, pct, cost) {
  if (!sessionId) return [];
  fs.mkdirSync(HIST_DIR, { recursive: true });
  const file = path.join(HIST_DIR, `${sessionId.replace(/[^\w.-]/g, "")}.json`);

  let samples = [];
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    if (Array.isArray(parsed)) samples = parsed;
  } catch {}

  const now = Date.now();
  const last = samples[samples.length - 1];
  if (!last || now - last.t >= MIN_SAMPLE_GAP_MS) {
    samples.push({ t: now, p: pct, c: cost });
    if (samples.length > MAX_SAMPLES) samples = samples.slice(-MAX_SAMPLES);
    try {
      writeJsonAtomic(file, samples);
      if (samples.length % 40 === 0) pruneOldHistory();
    } catch {}
  }
  return samples;
}

// Downsample `values` into `cells` buckets.
//   "last" — decimate, keeping the final reading of each bucket. Correct for a
//            LEVEL series: averaging would smooth a compaction cliff into a
//            gentle slope and hide the event we're trying to surface.
//   "sum"  — total per bucket. Correct for a RATE series: the spend inside a
//            bucket is the sum of its deltas, not their mean.
function bucket(values, cells, mode) {
  if (values.length <= cells) return values;
  const out = [];
  const size = values.length / cells;
  for (let i = 0; i < cells; i++) {
    const slice = values.slice(Math.floor(i * size), Math.floor((i + 1) * size));
    if (!slice.length) continue;
    out.push(mode === "sum" ? slice.reduce((a, b) => a + b, 0) : slice[slice.length - 1]);
  }
  return out;
}

// Absolute scale (0..max) — bar height is comparable across ticks.
function spark(values, { mode = "last", floorZero = true } = {}) {
  const nums = values.filter((n) => Number.isFinite(n));
  if (nums.length < 3) return null;
  const cells = bucket(nums, SPARK_CELLS, mode);
  const max = Math.max(...cells);
  if (max <= 0) return null;
  const min = floorZero ? 0 : Math.min(...cells);
  const span = max - min || max;
  return cells
    .map((n) => SPARK[Math.min(7, Math.max(0, Math.round(((n - min) / span) * 7)))])
    .join("");
}

const raw = readStdin();

// Always render powerline first — if anything below fails we still show it.
let out = "";
const bin = resolvePowerline();
if (bin) {
  // Omit --config when the user has no config file; powerline then uses its
  // own defaults instead of erroring on a path that does not exist.
  const args = fs.existsSync(CONFIG) ? [`--config=${CONFIG}`] : [];
  const res = spawnSync(bin, args, { input: raw, encoding: "utf8", timeout: 4000 });
  out = res.stdout || "";
}

// No powerline (not installed, or it failed): fall back to a bare line so the
// sparklines still have somewhere to attach and the status line is never blank.
if (!out.trim()) {
  try {
    const data = JSON.parse(raw);
    const dir = data?.workspace?.current_dir || data?.cwd || "";
    const model = data?.model?.display_name || "";
    out = [C.dim + (dir ? path.basename(dir) : "") + C.reset, model].filter(Boolean).join(" ") + "\n\n";
  } catch {
    out = "\n\n";
  }
}

try {
  const data = JSON.parse(raw);
  const pct = Number(data?.context_window?.used_percentage);
  const cost = Number(data?.cost?.total_cost_usd);
  const samples = recordSample(data?.session_id, pct, cost);

  const bits = [];

  // context level over time — drops mark auto-compactions
  const ctx = spark(samples.map((s) => s.p), { mode: "last" });
  if (ctx) bits.push(C.dim + "◔" + C.reset + " " + C.cyan + ctx + C.reset);

  // cost burn rate = delta between consecutive samples
  const costs = samples.map((s) => s.c).filter((n) => Number.isFinite(n));
  if (costs.length >= 4) {
    const deltas = [];
    for (let i = 1; i < costs.length; i++) deltas.push(Math.max(0, costs[i] - costs[i - 1]));
    const burn = spark(deltas, { mode: "sum" });
    if (burn) bits.push(C.dim + "$" + C.reset + " " + C.violet + burn + C.reset);
  }

  if (bits.length && out) {
    const lines = out.replace(/\n$/, "").split("\n");
    const idx = Math.min(TARGET_LINE, lines.length - 1);
    lines[idx] = lines[idx] + "  " + bits.join("  ");
    out = lines.join("\n") + "\n";
  }
} catch {
  // fall through with unmodified powerline output
}

process.stdout.write(out);
