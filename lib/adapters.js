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
    // --- Hook wiring (plan §4.4). Paths only; the merge logic is lib/hooks.js.
    hookConfig: (scope, cwd) =>
      scope === 'global'
        ? path.join(home(), '.claude', 'settings.json')
        : path.join(cwd, '.claude', 'settings.json'),
    // Claude Code nests lifecycle events under a "hooks" key.
    hookEventsAtRoot: false,
    hookEvent: 'Stop',
    hookScript: 'scripts/hook_stop.py',
    // No trust prompt: a settings.json entry is live as soon as it is written.
    hookTrustRequired: false,
    minVersionForHooks: null,
    notifyFallback: null, // Claude Code needs none
  },
  codex: {
    id: 'codex',
    label: 'OpenAI Codex CLI',
    rootDir: () => path.join(home(), '.codex'),
    memoryTarget: (scope, cwd) =>
      scope === 'global'
        ? path.join(home(), '.codex', 'AGENTS.md')
        : path.join(cwd, 'AGENTS.md'),
    // Codex-only installs get their own core here. A both-agents install shares
    // ONE core at the Claude Code root instead, and points this agent's wiring
    // back at it (plan §7) — see hooks.coreRoot().
    skillRoot: () => path.join(home(), '.codex', 'speakingwords'),
    // --- Hook wiring (plan §4.4). Paths only; the merge logic is lib/hooks.js.
    hookConfig: (scope, cwd) =>
      scope === 'global'
        ? path.join(home(), '.codex', 'hooks.json')
        : path.join(cwd, '.codex', 'hooks.json'),
    // Codex puts lifecycle events at the ROOT of hooks.json — no "hooks" key.
    // This is the only structural difference between the two adapters.
    hookEventsAtRoot: true,
    hookEvent: 'Stop', // Codex mirrors Claude Code's lifecycle names
    hookScript: 'scripts/hook_codex.py',
    // Codex will not run a hook until the user grants trust once. The installer
    // can only surface the step; it cannot grant it.
    hookTrustRequired: true,
    minVersionForHooks: '0.124.0', // below this, audit-only fallback (A13)
    // Pre-v0.124.0 degraded path: ~/.codex/config.toml `notify`, user-level
    // only, fires after the turn is already delivered — observe, never block.
    notifyFallback: 'agent-turn-complete',
    notifyConfig: () => path.join(home(), '.codex', 'config.toml'),
    notifyScript: 'scripts/notify_codex.py',
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
