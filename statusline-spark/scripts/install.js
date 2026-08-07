#!/usr/bin/env node
// install.js — wire statusline-spark into the user's settings.json.
//
//   node scripts/install.js              install or repair
//   node scripts/install.js --uninstall  remove
//   node scripts/install.js --status     report without changing anything
//
// Claude Code has no plugin-manifest key for `statusLine` / `subagentStatusLine`
// — they are settings.json fields — and a plugin's own install path is version
// stamped (…/cache/<marketplace>/<plugin>/<version>), so a settings entry that
// pointed into it would break on the next plugin update. So we copy the two
// renderers to a stable, user-owned directory and point settings.json there.

const fs = require("fs");
const os = require("os");
const path = require("path");

const SRC = path.resolve(__dirname, "..");
const CONFIG_DIR = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), ".claude");
const DEST = path.join(CONFIG_DIR, "statusline-spark");
const SETTINGS = path.join(CONFIG_DIR, "settings.json");
const POWERLINE_CONFIG = path.join(CONFIG_DIR, "claude-powerline.json");

const RENDERERS = ["statusline.js", "subagent-statusline.js"];

const mode = process.argv.includes("--uninstall")
  ? "uninstall"
  : process.argv.includes("--status")
    ? "status"
    : "install";

const notes = [];

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    if (e.code === "ENOENT") return fallback;
    throw new Error(`${file} does not parse as JSON — fix or move it, then re-run: ${e.message}`);
  }
}

// Never clobber settings.json in place: write a sibling then rename, so an
// interrupted run leaves the original intact rather than a truncated file.
function writeJsonAtomic(file, data) {
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n");
  fs.renameSync(tmp, file);
}

// settings.json is hand-edited config the user owns. Keep one dated copy per
// run so a bad merge is always recoverable.
function backup(file) {
  if (!fs.existsSync(file)) return null;
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const dest = `${file}.bak-statusline-spark-${stamp}`;
  fs.copyFileSync(file, dest);
  return dest;
}

// Absolute path with quoting, because a home directory containing a space would
// otherwise split into two argv entries.
const nodeCmd = (file) => `node "${path.join(DEST, file)}"`;

function findPowerline() {
  if (process.env.CLAUDE_POWERLINE_BIN) return process.env.CLAUDE_POWERLINE_BIN;
  const candidates = [path.join(os.homedir(), ".local", "bin", "claude-powerline")];
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir) candidates.push(path.join(dir, "claude-powerline"));
  }
  for (const candidate of candidates) {
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      return candidate;
    } catch {}
  }
  return null;
}

function reportStatus() {
  const settings = readJson(SETTINGS, {});
  const wired = (key, file) =>
    settings[key]?.command?.includes(path.join(DEST, file)) ? "wired" : "not wired";
  console.log(`config dir      ${CONFIG_DIR}`);
  console.log(`renderers       ${fs.existsSync(DEST) ? DEST : "not installed"}`);
  console.log(`statusLine      ${wired("statusLine", "statusline.js")}`);
  console.log(`subagentStatus  ${wired("subagentStatusLine", "subagent-statusline.js")}`);
  console.log(`powerline       ${findPowerline() || "NOT FOUND"}`);
  console.log(`powerline conf  ${fs.existsSync(POWERLINE_CONFIG) ? POWERLINE_CONFIG : "none (defaults)"}`);
}

function install() {
  // Parse settings.json before touching the filesystem. It is the only step
  // that can fail on user input, and failing after the copy would leave a
  // half-install: renderers on disk that nothing is wired to.
  const settings = readJson(SETTINGS, {});

  fs.mkdirSync(DEST, { recursive: true });
  for (const file of RENDERERS) {
    fs.copyFileSync(path.join(SRC, "hooks", file), path.join(DEST, file));
  }
  notes.push(`renderers installed to ${DEST}`);

  // The powerline config is the user's to tune; a re-install must not revert
  // their segment choices, so only seed it when there is nothing there.
  if (!fs.existsSync(POWERLINE_CONFIG)) {
    fs.copyFileSync(path.join(SRC, "config", "claude-powerline.json"), POWERLINE_CONFIG);
    notes.push(`seeded ${POWERLINE_CONFIG}`);
  } else {
    notes.push(`kept existing ${POWERLINE_CONFIG}`);
  }

  const previous = {
    statusLine: settings.statusLine?.command,
    subagentStatusLine: settings.subagentStatusLine?.command,
  };
  const saved = backup(SETTINGS);
  if (saved) notes.push(`backed up settings.json to ${path.basename(saved)}`);

  settings.statusLine = {
    type: "command",
    command: nodeCmd("statusline.js"),
    refreshInterval: settings.statusLine?.refreshInterval ?? 10,
  };
  settings.subagentStatusLine = { type: "command", command: nodeCmd("subagent-statusline.js") };
  writeJsonAtomic(SETTINGS, settings);
  notes.push("settings.json: statusLine + subagentStatusLine wired");

  for (const [key, cmd] of Object.entries(previous)) {
    if (cmd && !cmd.includes(DEST)) notes.push(`replaced previous ${key}: ${cmd}`);
  }

  const bin = findPowerline();
  if (bin) {
    notes.push(`claude-powerline found at ${bin}`);
  } else {
    notes.push(
      "claude-powerline NOT found — the status line still renders (bare fallback), " +
        "but install it for the full segments:\n    npm install -g @owloops/claude-powerline"
    );
  }
}

function uninstall() {
  const settings = readJson(SETTINGS, {});
  let touched = false;
  // Only strip entries that point at our directory. A user who has since moved
  // to a different status line should keep it.
  for (const [key, file] of [
    ["statusLine", "statusline.js"],
    ["subagentStatusLine", "subagent-statusline.js"],
  ]) {
    if (settings[key]?.command?.includes(path.join(DEST, file))) {
      delete settings[key];
      touched = true;
      notes.push(`settings.json: removed ${key}`);
    } else if (settings[key]) {
      notes.push(`settings.json: left ${key} alone (points elsewhere)`);
    }
  }
  if (touched) {
    const saved = backup(SETTINGS);
    if (saved) notes.push(`backed up settings.json to ${path.basename(saved)}`);
    writeJsonAtomic(SETTINGS, settings);
  }

  if (fs.existsSync(DEST)) {
    fs.rmSync(DEST, { recursive: true, force: true });
    notes.push(`removed ${DEST}`);
  }
  // Left in place deliberately: the powerline config and the session history are
  // user data, and claude-powerline may still be in use on its own.
  notes.push(`kept ${POWERLINE_CONFIG} and ${path.join(CONFIG_DIR, "statusline-history")}`);
}

try {
  if (mode === "status") {
    reportStatus();
  } else {
    if (mode === "install") install();
    else uninstall();
    for (const note of notes) console.log(`- ${note}`);
    console.log(`\n${mode} complete. Restart Claude Code to pick up the change.`);
  }
} catch (e) {
  console.error(`error: ${e.message}`);
  process.exit(1);
}
