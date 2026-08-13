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

const FILENAME = 'pref.json';

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

function writePref(pref) {
  const merged = { ...defaults(), ...pref };
  if (!Array.isArray(merged.agents) || merged.agents.length === 0) {
    throw new Error('pref.agents must be a non-empty array');
  }
  const file = prefPath(merged.agents);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  // Stable key order and a trailing newline keep repeat installs diff-free.
  const ordered = {
    agents: merged.agents,
    mode: merged.mode,
    scope: merged.scope,
    voice: merged.voice,
    version: merged.version,
  };
  fs.writeFileSync(file, `${JSON.stringify(ordered, null, 2)}\n`, 'utf8');
  return file;
}

module.exports = { FILENAME, prefPath, defaults, readPref, findPref, writePref };
