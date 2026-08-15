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
// `conciseness` is appended rather than slotted next to `voice` on purpose: the
// order of the first five keys is asserted in evals/run_p8.py, and a 0.1.0 file
// that already carried the key would have carried it at the end anyway.
// `register` joins the tail for the same reason, in v0.3.0. New axes are
// appended; nothing above them ever moves.
const KNOWN_KEYS = ['agents', 'mode', 'scope', 'voice', 'version', 'conciseness', 'register'];

// The two conciseness levels, and what a util does with anything else.
//
// The dial shipped as three positions during 0.2.0 development. The rig2
// recording (evals/records/e8-e9-v0.2.0-rig2.md) measured `low` at 16.1% and
// `med` at 25.4% — both inside their bands — while the old 40–50% `high` came
// in at 19.9%, undercutting its own floor. Two proven positions beat three
// where one is fiction, so `med`'s behaviour and band were promoted to become
// `high` and the old `high` band was dropped.
//
// `med` therefore survives as a recognised legacy value: a 0.2.0-dev pref.json
// still on disk normalises to `high` silently rather than crashing or bouncing
// the user back to a question they already answered.
//
// `null` means "never chosen" — only a 0.1.0 install can be in that state, and
// every reader treats it as `high`, the most aggressive shipped level, which is
// the band 0.1.0 already behaved in (plan §8). Preserving behaviour beats
// guessing at an intent nobody stated. Unknown values land there too: one
// fallback value everywhere (A19).
const LEVELS = ['low', 'high'];
const LEGACY_LEVELS = { med: 'high' };
const DEFAULT_LEVEL = 'high';      // what init suggests to a new install
const FALLBACK_LEVEL = 'high';     // what a reader assumes when the key is unset

/**
 * A shipped level, or `null` when the value is not one and not a legacy alias.
 * Callers that must produce a level anyway fall back to FALLBACK_LEVEL.
 */
function normaliseLevel(value) {
  if (typeof value !== 'string') return null;
  const candidate = value.trim().toLowerCase();
  if (LEVELS.includes(candidate)) return candidate;
  return LEGACY_LEVELS[candidate] || null;
}

/** The level a reader should act on, for any value at all. */
function conciseness(pref) {
  return normaliseLevel(pref && pref.conciseness) || FALLBACK_LEVEL;
}

// The two registers, and what a util does with anything else.
//
// Voice says what shape a reply takes, conciseness says how much of it
// survives, register says how the sentences are built. `ste` is inspired by
// ASD-STE100 Simplified Technical English: short sentences, no contractions,
// active voice, one instruction per sentence. It implements writing rules only
// — the approved-word dictionary is ASD's copyright and is not shipped,
// reproduced or approximated anywhere in this tree, and output is not
// conformant STE.
//
// `null` means "never chosen", which every install written before 0.3.0 is.
// All of those behaved as `slack`, so `slack` is both what init suggests and
// what a reader assumes when the key is absent (A33, A35). Unknown values land
// there too: one fallback value everywhere, and it is the one that changes
// nothing for a user who never asked for a change.
const REGISTERS = ['slack', 'ste'];
const DEFAULT_REGISTER = 'slack';   // what init suggests to a new install
const FALLBACK_REGISTER = 'slack';  // what a reader assumes when the key is unset

/** A shipped register, or `null` when the value is not one. */
function normaliseRegister(value) {
  if (typeof value !== 'string') return null;
  const candidate = value.trim().toLowerCase();
  return REGISTERS.includes(candidate) ? candidate : null;
}

/** The register a reader should act on, for any value at all. */
function register(pref) {
  return normaliseRegister(pref && pref.register) || FALLBACK_REGISTER;
}

function prefPath(agents) {
  const list = Array.isArray(agents) ? agents : [agents];
  const primary = list.includes('claude') ? 'claude' : list[0];
  if (!primary) throw new Error('prefPath() needs at least one agent');
  return path.join(getAdapter(primary).skillRoot(), FILENAME);
}

function defaults() {
  return {
    agents: [], mode: null, scope: null, voice: null, version: null,
    conciseness: null, register: null,
  };
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
  const drop = new Set(options.drop || []);

  // A key the caller left out is a key the caller has no opinion about, not an
  // instruction to erase it. `unhook` rewrites pref.json without mentioning
  // conciseness, and the user's level has to survive that (A25).
  const given = {};
  for (const [key, value] of Object.entries(pref || {})) {
    if (value !== undefined) given[key] = value;
  }

  if (!Array.isArray(given.agents) || given.agents.length === 0) {
    throw new Error('pref.agents must be a non-empty array');
  }
  const file = prefPath(given.agents);

  // Whatever the file already carries that this version has no opinion about
  // rides through the rewrite untouched (A25). The caller still wins wherever
  // the two disagree.
  let existing = {};
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) existing = parsed;
  } catch { /* absent, or unreadable — then there is nothing to preserve */ }

  const merged = { ...defaults(), ...existing, ...given };

  // Stable key order and a trailing newline keep repeat installs diff-free.
  const ordered = {};
  for (const key of KNOWN_KEYS) ordered[key] = merged[key];
  for (const [key, value] of Object.entries({ ...existing, ...given })) {
    if (!KNOWN_KEYS.includes(key) && !drop.has(key)) ordered[key] = value;
  }

  writeFileAtomic(file, `${JSON.stringify(ordered, null, 2)}\n`);
  return file;
}

module.exports = {
  FILENAME,
  KNOWN_KEYS,
  LEVELS,
  LEGACY_LEVELS,
  DEFAULT_LEVEL,
  FALLBACK_LEVEL,
  REGISTERS,
  DEFAULT_REGISTER,
  FALLBACK_REGISTER,
  normaliseLevel,
  conciseness,
  normaliseRegister,
  register,
  prefPath,
  defaults,
  readPref,
  findPref,
  writePref,
};
