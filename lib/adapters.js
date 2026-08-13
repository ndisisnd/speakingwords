'use strict';

// Per-agent wiring table (plan §4.4).
//
// Everything that defines *what good output is* lives in the shared core
// (skill/SKILL.md, skill/refs/lexicon.md, skill/scripts/lint.py) and is
// byte-identical across agents. This file only holds *where things plug in*.
//
// TEST SEAM: every home-relative path ("~/.claude", "~/.codex") resolves under
// process.env.SPEAKINGWORDS_HOME when that variable is set. Nothing else in the
// codebase may call os.homedir() directly. This keeps evals and CI out of the
// real home directory.

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function home() {
  return process.env.SPEAKINGWORDS_HOME || os.homedir();
}

const AGENTS = {
  claude: {
    id: 'claude',
    label: 'Claude Code',
    // Directory whose existence means the agent is installed.
    rootDir: () => path.join(home(), '.claude'),
    // Memory-mode write targets (plan §4.1, §4.4).
    memoryTarget: (scope, cwd) =>
      scope === 'global'
        ? path.join(home(), '.claude', 'CLAUDE.md')
        : path.join(cwd, 'CLAUDE.local.md'),
    // Where the installed skill tree (and pref.json) lives.
    skillRoot: () => path.join(home(), '.claude', 'skills', 'speakingwords'),
    // --- Placeholders, wired in later phases. Documented here so the adapter
    // shape is complete and P3/P4 only fill in behaviour, not structure. ---
    hookConfig: () => path.join(home(), '.claude', 'settings.json'), // P3
    hookEvent: 'Stop', // P3
    hookTrustRequired: false, // P3
    minVersionForHooks: null, // P3
    notifyFallback: null, // P3 — Claude Code needs none
  },
  codex: {
    id: 'codex',
    label: 'OpenAI Codex CLI',
    rootDir: () => path.join(home(), '.codex'),
    memoryTarget: (scope, cwd) =>
      scope === 'global'
        ? path.join(home(), '.codex', 'AGENTS.md')
        : path.join(cwd, 'AGENTS.md'),
    skillRoot: () => path.join(home(), '.codex', 'speakingwords'),
    // --- Placeholders, wired in P4. ---
    hookConfig: () => path.join(home(), '.codex', 'hooks.json'), // P4
    hookEvent: 'Stop', // P4 — Codex mirrors Claude Code's lifecycle names
    hookTrustRequired: true, // P4 — installer must surface the trust grant
    minVersionForHooks: '0.124.0', // P4 — below this, audit-only fallback
    notifyFallback: 'agent-turn-complete', // P4
  },
};

function getAdapter(agentId) {
  const adapter = AGENTS[agentId];
  if (!adapter) throw new Error(`Unknown agent: ${agentId}`);
  return adapter;
}

function listAgents() {
  return Object.values(AGENTS);
}

// An agent counts as present when its root config directory exists.
function isPresent(agentId) {
  try {
    return fs.statSync(getAdapter(agentId).rootDir()).isDirectory();
  } catch {
    return false;
  }
}

function detectAgents() {
  return listAgents().filter((a) => isPresent(a.id)).map((a) => a.id);
}

function memoryTarget(agentId, scope, cwd = process.cwd()) {
  if (scope !== 'local' && scope !== 'global') {
    throw new Error(`Unknown scope: ${scope}`);
  }
  return getAdapter(agentId).memoryTarget(scope, cwd);
}

module.exports = {
  AGENTS,
  home,
  getAdapter,
  listAgents,
  isPresent,
  detectAgents,
  memoryTarget,
};
