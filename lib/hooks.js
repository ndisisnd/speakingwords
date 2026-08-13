'use strict';

// Hook mode wiring for Claude Code (plan §4.2, §4.4, §7).
//
// Two jobs, kept separate on purpose:
//   1. Put the shipped skill core on disk at the installed root, so the hook
//      command path exists and `lint.py` can find its lexicon beside it.
//   2. Add exactly one Stop-hook entry to the right settings.json — global
//      (~/.claude/settings.json) or local (<cwd>/.claude/settings.json).
//
// The settings file belongs to the user, not to us. Every write here is a
// merge: unknown keys, key order, indentation and the trailing newline of the
// original file all survive. Install -> uninstall must leave the file
// byte-identical to how it was found; evals/run_p3.py asserts exactly that.
//
// Our entries are found by their command string containing "speakingwords",
// which is why the hook command is always an absolute path into the installed
// skill root.
//
// TEST SEAM: every "~" resolves through lib/adapters.js `home()`, which
// honours process.env.SPEAKINGWORDS_HOME.

const fs = require('node:fs');
const path = require('node:path');

const { getAdapter } = require('./adapters');

const REPO_SKILL_DIR = path.join(__dirname, '..', 'skill');
const HOOK_SCRIPT = 'scripts/hook_stop.py';
const TAG = 'speakingwords';

// ------------------------------------------------------------- settings path

function settingsPath(scope, cwd = process.cwd()) {
  if (scope === 'global') return path.join(getAdapter('claude').rootDir(), 'settings.json');
  if (scope === 'local') return path.join(cwd, '.claude', 'settings.json');
  throw new Error(`Unknown scope: ${scope}`);
}

function hookCommand(skillRoot) {
  return `python3 ${path.join(skillRoot, ...HOOK_SCRIPT.split('/'))}`;
}

// ------------------------------------------------------- copy-install core

// Recursive copy with no dependency on fs.cpSync options we do not need, so
// the file list is explicit and the installed tree stays inspectable.
function copyTree(from, to) {
  const copied = [];
  fs.mkdirSync(to, { recursive: true });
  for (const entry of fs.readdirSync(from, { withFileTypes: true })) {
    const src = path.join(from, entry.name);
    const dest = path.join(to, entry.name);
    if (entry.isDirectory()) {
      copied.push(...copyTree(src, dest));
    } else if (entry.isFile()) {
      fs.copyFileSync(src, dest);
      copied.push(dest);
    }
  }
  return copied;
}

/**
 * Replicate skill/ (SKILL.md, refs/, scripts/) into the installed skill root.
 *
 * Overwrites the files it ships and touches nothing else, so pref.json and
 * hits.jsonl sitting in the same directory survive a reinstall.
 */
function installSkillCore(agentId = 'claude') {
  const skillRoot = getAdapter(agentId).skillRoot();
  const files = copyTree(REPO_SKILL_DIR, skillRoot);
  // The hook is invoked as `python3 <path>`, but make it executable anyway so
  // a user can run it by hand while debugging.
  try {
    fs.chmodSync(path.join(skillRoot, ...HOOK_SCRIPT.split('/')), 0o755);
  } catch { /* non-fatal: mode is a convenience, not a requirement */ }
  return { skillRoot, files };
}

// ---------------------------------------------------------- settings merge

// Match the original file's shape so a round trip is byte-identical.
function detectFormat(text) {
  if (text === null) return { indent: 2, trailingNewline: true };
  const match = text.match(/\n(\t| +)(?=["}\]])/);
  let indent = 2;
  if (match) indent = match[1] === '\t' ? '\t' : match[1].length;
  return { indent, trailingNewline: /\n$/.test(text) };
}

function readSettings(file) {
  let text = null;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
  }
  const format = detectFormat(text);
  let data = {};
  if (text !== null && text.trim()) {
    try {
      data = JSON.parse(text);
    } catch (err) {
      throw new Error(
        `${file} is not valid JSON (${err.message}). Fix or move it, then re-run — ` +
        'speakingwords will not overwrite a settings file it cannot read.'
      );
    }
    if (data === null || typeof data !== 'object' || Array.isArray(data)) {
      throw new Error(`${file} does not contain a JSON object.`);
    }
  }
  return { text, data, format };
}

function writeSettings(file, data, format) {
  const body = JSON.stringify(data, null, format.indent);
  const next = format.trailingNewline ? `${body}\n` : body;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, next, 'utf8');
  return next;
}

function isOurs(entry) {
  return Boolean(entry) && typeof entry.command === 'string' && entry.command.includes(TAG);
}

/**
 * Add (or refresh) the Stop-hook entry in the chosen settings file.
 *
 * Idempotent: a second install rewrites our own entry in place rather than
 * appending a duplicate, so install -> install produces zero diff.
 *
 * @returns {{path: string, command: string, action: 'created'|'added'|'updated'|'unchanged'}}
 */
function installClaudeHook({ scope, cwd = process.cwd(), skillRoot } = {}) {
  const root = skillRoot || getAdapter('claude').skillRoot();
  const file = settingsPath(scope, cwd);
  const { text, data, format } = readSettings(file);
  const command = hookCommand(root);

  if (!data.hooks || typeof data.hooks !== 'object' || Array.isArray(data.hooks)) {
    data.hooks = {};
  }
  if (!Array.isArray(data.hooks.Stop)) data.hooks.Stop = [];

  const groups = data.hooks.Stop;
  let found = false;
  for (const group of groups) {
    if (!group || !Array.isArray(group.hooks)) continue;
    for (const entry of group.hooks) {
      if (!isOurs(entry)) continue;
      if (found) {
        entry._speakingwordsDuplicate = true; // marked, pruned below
        continue;
      }
      found = true;
      entry.type = 'command';
      entry.command = command;
    }
  }

  if (found) {
    // Prune any duplicates a hand-edit may have left behind.
    for (const group of groups) {
      if (!group || !Array.isArray(group.hooks)) continue;
      group.hooks = group.hooks.filter((e) => !(e && e._speakingwordsDuplicate));
    }
    data.hooks.Stop = groups.filter((g) => !g || !Array.isArray(g.hooks) || g.hooks.length > 0);
  } else {
    groups.push({ hooks: [{ type: 'command', command }] });
  }

  const next = writeSettings(file, data, format);
  let action;
  if (text === null) action = 'created';
  else if (next === text) action = 'unchanged';
  else if (found) action = 'updated';
  else action = 'added';

  return { path: file, command, action };
}

/**
 * Remove every speakingwords Stop-hook entry from the chosen settings file.
 *
 * Tidies up after itself: an emptied group goes, an emptied `hooks.Stop` goes,
 * an emptied `hooks` goes. Nothing else is touched.
 *
 * @returns {{path: string, removed: number, action: 'removed'|'absent'|'missing'}}
 */
function uninstallClaudeHook({ scope, cwd = process.cwd() } = {}) {
  const file = settingsPath(scope, cwd);
  const { text, data, format } = readSettings(file);
  if (text === null) return { path: file, removed: 0, action: 'missing' };

  let removed = 0;
  const hooks = data.hooks;
  if (hooks && typeof hooks === 'object' && Array.isArray(hooks.Stop)) {
    const kept = [];
    for (const group of hooks.Stop) {
      if (!group || !Array.isArray(group.hooks)) {
        kept.push(group);
        continue;
      }
      const before = group.hooks.length;
      group.hooks = group.hooks.filter((entry) => !isOurs(entry));
      removed += before - group.hooks.length;
      // A group that only ever held our entry goes with it; a group that
      // carried other keys (a matcher, say) is kept only if it still has work.
      if (group.hooks.length > 0) kept.push(group);
    }
    hooks.Stop = kept;
    if (hooks.Stop.length === 0) delete hooks.Stop;
    if (Object.keys(hooks).length === 0) delete data.hooks;
  }

  if (removed === 0) return { path: file, removed: 0, action: 'absent' };
  writeSettings(file, data, format);
  return { path: file, removed, action: 'removed' };
}

/** Is our Stop hook currently wired in this scope? */
function isInstalled({ scope, cwd = process.cwd() } = {}) {
  const { data } = readSettings(settingsPath(scope, cwd));
  const stop = data.hooks && data.hooks.Stop;
  if (!Array.isArray(stop)) return false;
  return stop.some((g) => g && Array.isArray(g.hooks) && g.hooks.some(isOurs));
}

module.exports = {
  TAG,
  HOOK_SCRIPT,
  settingsPath,
  hookCommand,
  installSkillCore,
  installClaudeHook,
  uninstallClaudeHook,
  isInstalled,
};
