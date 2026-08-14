'use strict';

// pref.json — the single record of what was installed, where, and how.
// Every util (`status`, `update`, `unhook`) reads it, so it is written last in
// the init flow, only after the memory block landed.
//
// Location follows the installed skill root (plan §4.4 / §7):
//   claude chosen  -> ~/.claude/skills/speakingwords/pref.json
//   codex only     -> ~/.codex/speakingwords/pref.json
// When both agents are installed they share one core, so one pref file at the
// Claude root is the record for both; `agents` names them.
//
// TEST SEAM: "~" resolves through lib/adapters.js `home()`, which honours
// process.env.SPEAKINGWORDS_HOME. Set that variable and nothing touches the
// real home directory. Evals and CI rely on it.

const fs = require('node:fs');
const path = require('node:path');

const { getAdapter } = require('./adapters');
const { writeFileAtomic } = require('./atomic');

const FILENAME = 'pref.json';

// The keys this version writes, in the order it writes them. Anything else the
// file carries is a key from another version, and is kept (A25): a 0.2.0 util
// must not silently strip a 0.1.0 field, and a 0.1.0 util must not strip a
// 0.2.0 one. Forward compatibility here is what makes upgrading in place — and
// downgrading again — a non-event.
const KNOWN_KEYS = ['agents', 'mode', 'scope', 'voice', 'version'];

function prefPath(agents) {
  const list = Array.isArray(agents) ? agents : [agents];
  const primary = list.includes('claude') ? 'claude' : list[0];
  if (!primary) throw new Error('prefPath() needs at least one agent');
  return path.join(getAdapter(primary).skillRoot(), FILENAME);
}

function defaults() {
  return { agents: [], mode: null, scope: null, voice: null, version: null };
}

function readPref(agents) {
  const file = prefPath(agents);
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
}

// Look in every known install root, for callers that do not yet know the agent.
function findPref() {
  for (const agent of ['claude', 'codex']) {
    const file = path.join(getAdapter(agent).skillRoot(), FILENAME);
    try {
      return { path: file, pref: JSON.parse(fs.readFileSync(file, 'utf8')) };
    } catch (err) {
      if (err.code !== 'ENOENT') throw err;
    }
  }
  return null;
}

/**
 * Write pref.json.
 *
 * @param {object} pref
 * @param {object} [options]
 * @param {string[]} [options.drop] unknown keys to remove rather than preserve.
 *   Preservation is the default, so a caller that genuinely wants a stale key
 *   gone — `status` clearing a degradation that has healed — has to say so.
 */
function writePref(pref, options = {}) {
  const merged = { ...defaults(), ...pref };
  const drop = new Set(options.drop || []);
  if (!Array.isArray(merged.agents) || merged.agents.length === 0) {
    throw new Error('pref.agents must be a non-empty array');
  }
  const file = prefPath(merged.agents);

  // Whatever the file already carries that this version has no opinion about
  // rides through the rewrite untouched (A25). The caller still wins wherever
  // the two disagree.
  let existing = {};
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) existing = parsed;
  } catch { /* absent, or unreadable — then there is nothing to preserve */ }

  // Stable key order and a trailing newline keep repeat installs diff-free.
  const ordered = {};
  for (const key of KNOWN_KEYS) ordered[key] = merged[key];
  for (const [key, value] of Object.entries({ ...existing, ...pref })) {
    if (!KNOWN_KEYS.includes(key) && !drop.has(key)) ordered[key] = value;
  }

  writeFileAtomic(file, `${JSON.stringify(ordered, null, 2)}\n`);
  return file;
}

module.exports = { FILENAME, KNOWN_KEYS, prefPath, defaults, readPref, findPref, writePref };
