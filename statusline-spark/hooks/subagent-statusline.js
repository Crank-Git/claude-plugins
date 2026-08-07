#!/usr/bin/env node
// subagent-statusline — custom row renderer for Claude Code's agent panel.
//
// Installed by `/statusline-spark:install`, which copies this file to
// $CLAUDE_CONFIG_DIR/statusline-spark/ and wires settings.json:
//   "subagentStatusLine": { "type": "command", "command": "node \"…/subagent-statusline.js\"" }
//
// Receives one JSON object on stdin per refresh tick:
//   { columns: <int>, tasks: [{ id, name, type, status, description, label,
//                               startTime, model, effort, contextWindowSize,
//                               tokenCount, tokenSamples, cwd }] }
// Emits one JSON line per row to override: {"id": "<task id>", "content": "<row>"}
//
// Row layout:
//   <status> <name> (<model>/<effort>) <ctx bar> <pct> <tokens> <elapsed> <rate> <description>
//
// Docs: https://code.claude.com/docs/en/statusline#subagent-status-lines

const C = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  cyan: "\x1b[38;5;51m",
  green: "\x1b[38;5;84m",
  red: "\x1b[38;5;203m",
  yellow: "\x1b[38;5;221m",
  orange: "\x1b[38;5;215m",
  gray: "\x1b[38;5;245m",
  blue: "\x1b[38;5;111m",
  violet: "\x1b[38;5;177m",
};

// status -> [icon, color]. Unknown statuses fall through to the default.
const STATUS = {
  running: ["⟳", C.cyan],
  in_progress: ["⟳", C.cyan],
  active: ["⟳", C.cyan],
  pending: ["◌", C.gray],
  queued: ["◌", C.gray],
  completed: ["✓", C.green],
  done: ["✓", C.green],
  success: ["✓", C.green],
  failed: ["✗", C.red],
  error: ["✗", C.red],
  cancelled: ["⊘", C.gray],
  killed: ["⊘", C.gray],
};

const EFFORT_COLOR = {
  low: C.gray,
  medium: C.blue,
  high: C.yellow,
  xhigh: C.orange,
  max: C.red,
};

function fmtTokens(n) {
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e5 ? 0 : 1) + "k";
  return String(n);
}

function fmtElapsed(ms) {
  if (!Number.isFinite(ms) || ms < 0) return null;
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m" + String(s % 60).padStart(2, "0") + "s";
  const h = Math.floor(m / 60);
  return h + "h" + String(m % 60).padStart(2, "0") + "m";
}

// startTime may arrive as epoch ms, epoch seconds, or an ISO string. Anything
// that lands at or before the epoch (0, -1000, "0") is garbage, not a start
// time — reject it rather than rendering ⧗233054h46m.
// 2023-01-01. Deliberately later than Date.parse("0") (which V8 resolves to
// 2000-01-01), so a bare "0" is rejected rather than becoming a 233054h clock.
const EARLIEST_PLAUSIBLE_MS = 1672531200000;
function toMillis(t) {
  let ms = null;
  if (typeof t === "number" && Number.isFinite(t)) {
    ms = t > 1e11 ? t : t * 1000;
  } else if (typeof t === "string") {
    const n = Number(t);
    if (Number.isFinite(n) && n > 0) ms = n > 1e11 ? n : n * 1000;
    else {
      const parsed = Date.parse(t);
      if (!Number.isNaN(parsed)) ms = parsed;
    }
  }
  return Number.isFinite(ms) && ms >= EARLIEST_PLAUSIBLE_MS ? ms : null;
}

// floor, not round: a bar must not read full until it actually is full.
function bar(pct, width) {
  const filled = Math.max(0, Math.min(width, Math.floor((pct / 100) * width)));
  return "█".repeat(filled) + "░".repeat(width - filled);
}

function ctxColor(pct) {
  if (pct >= 85) return C.red;
  if (pct >= 60) return C.yellow;
  return C.green;
}

function shortModel(id) {
  if (!id || typeof id !== "string") return null;
  const m = id.toLowerCase();
  for (const fam of ["opus", "sonnet", "haiku", "fable"]) {
    if (m.includes(fam)) return fam + (m.includes("[1m]") ? "-1m" : "");
  }
  return clean(id, 14);
}

// Glyphs that occupy two terminal cells. Width math must count cells, not
// UTF-16 code units, or a CJK/emoji description overflows the panel.
const WIDE = /[ᄀ-ᅟ⺀-꓏ꥠ-꥿가-힣豈-﫿︰-﹯＀-｠￠-￦]/;
function cellWidth(str) {
  let w = 0;
  for (const ch of str) {
    const cp = ch.codePointAt(0);
    if (cp >= 0x1f300 || WIDE.test(ch)) w += 2;
    else w += 1;
  }
  return w;
}

const visLen = (s) => cellWidth(s.replace(/\x1b\[[0-9;]*m/g, ""));

// task.name / description / label / effort / model originate in agent prompts,
// and Claude Code renders our `content` verbatim including escape sequences.
// Coerce to string and strip C0/C1 controls (cursor moves, screen clears, OSC
// title/clipboard) plus bidi/zero-width format chars, which can otherwise
// reorder or hide the rest of the rendered row.
const STRIP =
  /[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠-⁤⁦-⁩﻿]/g;
function clean(value, maxLen) {
  if (value == null) return "";
  let s = typeof value === "string" ? value : String(value);
  s = s.replace(STRIP, "").replace(/\s+/g, " ").trim();
  if (maxLen && cellWidth(s) > maxLen) {
    // Truncate by code point (never split a surrogate pair) and by cell width.
    let w = 0;
    let out = "";
    for (const ch of s) {
      const cw = cellWidth(ch);
      if (w + cw > maxLen - 1) break;
      out += ch;
      w += cw;
    }
    s = out + "…";
  }
  return s;
}

// Claude Code only sets `name` for named/addressable agents; everything else
// arrives with just a `type`. Map the raw type to something readable.
const TYPE_LABEL = {
  local_agent: "agent",
  local_bash: "bash",
  local_workflow: "workflow",
  remote_agent: "remote",
  in_process_teammate: "teammate",
};

const SPARK = "▁▂▃▄▅▆▇█";

// tokenSamples is a running series of cumulative tokenCount readings. Plotting
// it directly is useless — a cumulative series only rises, so every agent looks
// like the same ramp. Plot the per-tick DELTA instead: bar height is tokens
// added since the previous sample, so bursts of work stand out and a stalled
// agent (blocked on a long tool call) reads as a flat run of ▁.
function sparkline(samples, width) {
  if (!Array.isArray(samples)) return null;
  const nums = samples.filter((n) => Number.isFinite(n));
  if (nums.length < 3) return null;
  const deltas = [];
  for (let i = 1; i < nums.length; i++) deltas.push(Math.max(0, nums[i] - nums[i - 1]));
  const tail = deltas.slice(-width);
  const max = Math.max(...tail);
  if (max <= 0) return null;
  return tail
    .map((d) => SPARK[Math.min(SPARK.length - 1, Math.round((d / max) * (SPARK.length - 1)))])
    .join("");
}

// Plain-object lookups must be own-property checks: task.status of
// "constructor" or "__proto__" otherwise resolves up the prototype chain and
// yields a function or [object Object] instead of missing.
const lookup = (table, key) =>
  typeof key === "string" && Object.hasOwn(table, key) ? table[key] : undefined;

function renderRow(task, columns) {
  const [icon, color] =
    lookup(STATUS, String(task.status ?? "").toLowerCase()) || ["•", C.gray];
  const parts = [];

  parts.push(color + icon + C.reset);
  const title =
    clean(task.name, 24) || lookup(TYPE_LABEL, task.type) || clean(task.type, 24) || "agent";
  parts.push(C.bold + title + C.reset);

  // model / effort
  const model = shortModel(task.model);
  const effort = task.effort == null ? null : clean(task.effort, 12);
  if (model || effort) {
    const eColor = lookup(EFFORT_COLOR, effort) || C.violet;
    const inner = [
      model ? C.blue + model + C.reset : null,
      effort ? eColor + effort + C.reset : null,
    ].filter(Boolean).join(C.dim + "/" + C.reset);
    parts.push(C.dim + "(" + C.reset + inner + C.dim + ")" + C.reset);
  }

  // context usage — tokenCount is 0 on the first tick, so show the bar as soon
  // as we know the window rather than waiting for a nonzero count.
  const tokens = Number(task.tokenCount);
  const window = Number(task.contextWindowSize);
  if (Number.isFinite(tokens) && tokens >= 0 && Number.isFinite(window) && window > 0) {
    const pct = Math.min(100, (tokens / window) * 100);
    const cc = ctxColor(pct);
    parts.push(
      cc + bar(pct, 8) + C.reset + " " +
      cc + (pct >= 1 ? Math.floor(pct) + "%" : "<1%") + C.reset +
      C.dim + " " + (fmtTokens(tokens) || "0") + C.reset
    );
  } else if (Number.isFinite(tokens) && tokens > 0) {
    parts.push(C.dim + fmtTokens(tokens) + " tok" + C.reset);
  }

  // token growth over time
  const spark = sparkline(task.tokenSamples, 10);
  if (spark) parts.push(C.violet + spark + C.reset);

  // elapsed + token rate
  const started = toMillis(task.startTime);
  if (started && Date.now() - started >= 0) {
    const ms = Date.now() - started;
    const el = fmtElapsed(ms);
    if (el) parts.push(C.dim + "⧗" + el + C.reset);
    if (Number.isFinite(tokens) && tokens > 0 && ms > 5000) {
      const rate = fmtTokens(Math.round(tokens / (ms / 1000)));
      if (rate) parts.push(C.dim + "↑" + rate + "/s" + C.reset);
    }
  }

  const head = parts.join(" ");

  // description fills whatever width is left. `label` usually duplicates
  // `description`; only append it when it actually differs.
  let desc = clean(task.description);
  const label = clean(task.label);
  if (label && label !== desc) desc = desc ? `${desc} · ${label}` : label;
  if (!desc) return head;
  // -1 for the single separating space emitted below. Number(null) is 0, so
  // test the value rather than just its finiteness.
  const width = Number(columns) > 0 ? Number(columns) : 100;
  const room = width - visLen(head) - 1;
  if (room < 8) return head;
  return head + " " + C.gray + clean(desc, room) + C.reset;
}

// Debug tap: set CLAUDE_SUBAGENT_STATUSLINE_DEBUG to a file path to log the raw
// stdin payload and the emitted rows. Unset = off.
const DEBUG_LOG = process.env.CLAUDE_SUBAGENT_STATUSLINE_DEBUG || null;
function dbg(label, payload) {
  if (!DEBUG_LOG) return;
  try {
    require("fs").appendFileSync(
      DEBUG_LOG,
      `[${new Date().toISOString()}] ${label}: ${payload}\n`
    );
  } catch {}
}

dbg("INVOKED", "pid=" + process.pid + " tty=" + process.stdout.isTTY);

let raw = "";
let done = false;

function emit() {
  if (done) return;
  done = true;
  dbg("STDIN", raw.slice(0, 4000) || "<empty>");
  try {
    const input = JSON.parse(raw);
    const tasks = Array.isArray(input.tasks) ? input.tasks : [];
    const columns = input.columns;
    dbg("TASKS", `count=${tasks.length} columns=${columns}`);
    let out = "";
    for (const task of tasks) {
      if (!task || typeof task !== "object") continue;
      // The schema wants a string id, but a numeric one is still a valid
      // identity — coerce rather than dropping the row entirely.
      const id = task.id == null ? "" : String(task.id);
      if (!id) continue;
      // Per-task guard: one malformed task must not blank every other row.
      try {
        out += JSON.stringify({ id, content: renderRow(task, columns) }) + "\n";
      } catch (e) {
        dbg("ROW_ERROR", `${id}: ${e && e.stack ? e.stack : e}`);
      }
    }
    process.stdout.write(out);
    dbg("OUT", out || "<none>");
  } catch (e) {
    dbg("ERROR", String(e && e.stack ? e.stack : e));
    // Emit nothing on bad input so Claude Code falls back to default rows.
  }
}

// The panel can close the pipe mid-write; an unhandled EPIPE would surface as
// an uncaught exception rather than a dropped row.
process.stdout.on("error", () => {});

process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", emit);
process.stdin.on("error", (e) => {
  dbg("STDIN_ERROR", String(e));
  emit();
});
// Failsafe for a stdin that never closes. Only latch `done` if what we have so
// far actually parses — otherwise a slow tail would be discarded and the real
// `end` event ignored, losing the tick entirely.
setTimeout(() => {
  try {
    JSON.parse(raw);
  } catch {
    dbg("FAILSAFE_SKIP", "partial payload at 1.5s, waiting for end");
    return;
  }
  emit();
}, 1500).unref?.();
