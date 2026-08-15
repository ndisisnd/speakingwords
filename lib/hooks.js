'use strict';

// Hook mode wiring for Claude Code and Codex CLI (plan §4.2, §4.4, §7).
//
// Three jobs, kept separate on purpose:
//   1. Put the shipped skill core on disk at the installed root, so the hook
//      command path exists and `lint.py` can find its lexicon beside it.
//   2. Add exactly one Stop entry and one SessionStart entry to the right
//      config file — Claude Code's settings.json, or Codex's hooks.json.
//   3. On a Codex too old for hooks, wire the `notify` audit fallback instead.
//
// These config files belong to the user, not to us. Every write here is a
// merge: unknown keys, key order, indentation and the trailing newline of the
// original file all survive. Install -> uninstall must leave the file
// byte-identical to how it was found; run_p3.py and run_p4.py assert that.
//
// Our entries are found by their command string containing "speakingwords",
// which is why the hook command is always an absolute path into the installed
// skill root.
//
// Claude Code nests events under a "hooks" key; Codex puts them at the root of
// hooks.json. That one structural difference is the only thing separating the
// two install paths, so both go through the same merge helpers with a different
// container object.
//
// TEST SEAM: every "~" resolves through lib/adapters.js `home()`, which
// honours process.env.SPEAKINGWORDS_HOME. The installed Codex version resolves
// through process.env.SPEAKINGWORDS_CODEX_VERSION before any `codex --version`
// call is attempted, so evals never depend on a real Codex being present.

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const { getAdapter } = require('./adapters');
const { writeFileAtomic } = require('./atomic');

const REPO_SKILL_DIR = path.join(__dirname, '..', 'skill');
const HOOK_SCRIPT = 'scripts/hook_stop.py';
const CODEX_HOOK_SCRIPT = 'scripts/hook_codex.py';
const CODEX_NOTIFY_SCRIPT = 'scripts/notify_codex.py';
// Every hook command goes through the wrapper, not straight at python3: the
// wrapper probes for the interpreter and exits clean when it is absent (A24).
const HOOK_GUARD_SCRIPT = 'scripts/hook_guard.sh';
// Upstream prevention (plan §2 W2): a SessionStart hook states the active style
// rules once, early in the session, so fewer replies need bouncing at all. It is
// an addition to the Stop hook, never a replacement — enforcement still lives at
// Stop, and a SessionStart hook that never runs costs nothing (A26).
//
// Both agents get it (plan §8, resolved). Codex CLI fires the same SessionStart
// event, hands the hook the same stdin payload, and reads back the same
// `hookSpecificOutput` shape as Claude Code — so one script serves both and the
// only difference is which config file names it.
const SESSION_HOOK_SCRIPT = 'scripts/hook_session.py';
const TAG = 'speakingwords';
const EVENT = 'Stop';
const SESSION_EVENT = 'SessionStart';

// Codex's hooks engine is only stable from here. Below it, hook mode degrades
// to the audit-only `notify` fallback (plan A13).
const CODEX_MIN_HOOK_VERSION = '0.124.0';

// ------------------------------------------------------------- config paths

function settingsPath(scope, cwd = process.cwd()) {
  if (scope === 'global') return path.join(getAdapter('claude').rootDir(), 'settings.json');
  if (scope === 'local') return path.join(cwd, '.claude', 'settings.json');
  throw new Error(`Unknown scope: ${scope}`);
}

// Codex reads ~/.codex/hooks.json globally and <cwd>/.codex/hooks.json per
// project — same split as Claude Code, different filename.
function codexHooksPath(scope, cwd = process.cwd()) {
  if (scope === 'global') return path.join(getAdapter('codex').rootDir(), 'hooks.json');
  if (scope === 'local') return path.join(cwd, '.codex', 'hooks.json');
  throw new Error(`Unknown scope: ${scope}`);
}

// The notify fallback is user-level only — Codex has no per-project config.toml.
function codexConfigPath() {
  return path.join(getAdapter('codex').rootDir(), 'config.toml');
}

function guardPath(skillRoot) {
  return path.join(skillRoot, ...HOOK_GUARD_SCRIPT.split('/'));
}

function hookCommand(skillRoot) {
  return `sh ${guardPath(skillRoot)} ${path.join(skillRoot, ...HOOK_SCRIPT.split('/'))}`;
}

// The SessionStart injector goes through the same guard wrapper as the Stop
// hook, so a machine with no python3 gets the same clean exit here too (A24).
function sessionHookCommand(skillRoot) {
  return `sh ${guardPath(skillRoot)} ${path.join(skillRoot, ...SESSION_HOOK_SCRIPT.split('/'))}`;
}

function codexHookCommand(skillRoot) {
  return `sh ${guardPath(skillRoot)} ${path.join(skillRoot, ...CODEX_HOOK_SCRIPT.split('/'))}`;
}

function codexNotifyScript(skillRoot) {
  return path.join(skillRoot, ...CODEX_NOTIFY_SCRIPT.split('/'));
}

/**
 * Where the shared skill core lives for a given install.
 *
 * Plan §7: a both-agents install ships ONE core, at the Claude Code root, and
 * the Codex wiring points back at it. That is what makes `update` edit one
 * lexicon and change behaviour on both agents. Matches lib/pref.js prefPath().
 */
function coreRoot(agentIds) {
  const list = Array.isArray(agentIds) ? agentIds : [agentIds];
  const primary = list.includes('claude') ? 'claude' : list[0];
  if (!primary) throw new Error('coreRoot() needs at least one agent');
  return getAdapter(primary).skillRoot();
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
 *
 * Takes an agent id, a list of agent ids, or an explicit root. A list installs
 * one shared core (plan A11): the bytes are identical whichever agent asked for
 * them, because the same repo tree is copied to a single destination.
 */
function installSkillCore(agent = 'claude', explicitRoot) {
  const skillRoot = explicitRoot || coreRoot(agent);
  const files = copyTree(REPO_SKILL_DIR, skillRoot);
  // The hooks are invoked through `sh hook_guard.sh <path>`, but make them
  // executable anyway so a user can run them by hand while debugging.
  for (const rel of [
    HOOK_SCRIPT, SESSION_HOOK_SCRIPT, CODEX_HOOK_SCRIPT, CODEX_NOTIFY_SCRIPT, HOOK_GUARD_SCRIPT,
  ]) {
    try {
      fs.chmodSync(path.join(skillRoot, ...rel.split('/')), 0o755);
    } catch { /* non-fatal: mode is a convenience, not a requirement */ }
  }
  return { skillRoot, files };
}

// ------------------------------------------------------- Codex version gate

function parseSemver(text) {
  const match = String(text).match(/(\d+)\.(\d+)\.(\d+)/);
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

function compareSemver(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return 0;
}

/**
 * The installed Codex version, or null when it cannot be determined.
 *
 * null is not "old" — it is "unknown", and the caller reports it as such. A
 * missing binary, a `codex` that fails to run, or output without a semver in
 * it all land here.
 */
function detectCodexVersion() {
  const override = process.env.SPEAKINGWORDS_CODEX_VERSION;
  if (override) return parseSemver(override) ? override.trim() : null;
  try {
    const out = execFileSync('codex', ['--version'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 5000,
    });
    const parsed = parseSemver(out);
    return parsed ? parsed.join('.') : null;
  } catch {
    return null;
  }
}

/**
 * Can this Codex run hooks?
 *
 * An unknown version is treated as capable. Wiring hooks.json on a Codex that
 * ignores it costs one unread config entry; downgrading a capable Codex to
 * audit-only silently costs the user the enforcement they asked for.
 */
function codexSupportsHooks(version) {
  if (!version) return true;
  const parsed = parseSemver(version);
  if (!parsed) return true;
  return compareSemver(parsed, parseSemver(CODEX_MIN_HOOK_VERSION)) >= 0;
}

/** Version detection plus the resulting plan, in one call for the installer. */
function codexCapability() {
  const version = detectCodexVersion();
  const supportsHooks = codexSupportsHooks(version);
  return {
    version,
    supportsHooks,
    minVersion: CODEX_MIN_HOOK_VERSION,
    // "downgraded" means we knew the version and it was too old — the A13 path.
    downgraded: Boolean(version) && !supportsHooks,
  };
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
  // Atomic (A22): this is the user's own settings file, and the agent reads
  // it at times of its choosing.
  writeFileAtomic(file, next);
  return next;
}

function isOurs(entry) {
  return Boolean(entry) && typeof entry.command === 'string' && entry.command.includes(TAG);
}

/**
 * Add (or refresh) our entry inside an events container.
 *
 * `events` is whatever object holds the lifecycle event names: Claude Code's
 * `data.hooks`, or the root of Codex's hooks.json. The group/entry shape below
 * it is identical on both agents, so this is the only merge code either needs.
 *
 * Idempotent: a second install rewrites our own entry in place rather than
 * appending a duplicate, so install -> install produces zero diff.
 *
 * @returns {boolean} true when an existing entry of ours was refreshed
 */
function addHookEntry(events, command, event = EVENT) {
  if (!Array.isArray(events[event])) events[event] = [];
  const groups = events[event];

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
    events[event] = groups.filter((g) => !g || !Array.isArray(g.hooks) || g.hooks.length > 0);
  } else {
    groups.push({ hooks: [{ type: 'command', command }] });
  }
  return found;
}

/**
 * Remove every entry of ours from an events container and tidy what empties.
 *
 * @returns {number} how many entries were removed
 */
function removeHookEntry(events, event = EVENT) {
  if (!events || typeof events !== 'object' || !Array.isArray(events[event])) return 0;

  let removed = 0;
  const kept = [];
  for (const group of events[event]) {
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
  events[event] = kept;
  if (events[event].length === 0) delete events[event];
  return removed;
}

// Which action word describes what just happened to the file on disk.
function actionFor(text, next, found) {
  if (text === null) return 'created';
  if (next === text) return 'unchanged';
  return found ? 'updated' : 'added';
}

/**
 * Add (or refresh) both Claude Code entries in the chosen settings file.
 *
 * Two events, one write: `Stop` enforces after the fact, `SessionStart` states
 * the rules up front so there is less to enforce. Codex gets the same pair, in
 * its own config file — see installCodexHook (plan §8, resolved).
 *
 * `injector: false` wires the Stop hook alone. That is what `mode: both` asks
 * for (plan v0.3.0 W6, A31): the memory block already carries the contract into
 * every session, so injecting it again would state the same rules twice and buy
 * nothing. An injector already in the file is removed rather than left behind,
 * so switching a hook install to `both` ends with exactly one copy of the
 * contract, not two.
 *
 * @param {object} [options]
 * @param {boolean} [options.injector=true] wire the SessionStart injector too
 * @returns {{path: string, command: string, sessionCommand: string|null, action: 'created'|'added'|'updated'|'unchanged'}}
 */
function installClaudeHook({ scope, cwd = process.cwd(), skillRoot, injector = true } = {}) {
  const root = skillRoot || getAdapter('claude').skillRoot();
  const file = settingsPath(scope, cwd);
  const { text, data, format } = readSettings(file);
  const command = hookCommand(root);
  const sessionCommand = injector ? sessionHookCommand(root) : null;

  // Claude Code nests events one level down, under "hooks".
  if (!data.hooks || typeof data.hooks !== 'object' || Array.isArray(data.hooks)) {
    data.hooks = {};
  }
  const found = addHookEntry(data.hooks, command);
  let foundSession = true;
  if (injector) {
    foundSession = addHookEntry(data.hooks, sessionCommand, SESSION_EVENT);
  } else {
    removeHookEntry(data.hooks, SESSION_EVENT);
  }
  const next = writeSettings(file, data, format);

  return {
    path: file,
    command,
    sessionCommand,
    action: actionFor(text, next, found && foundSession),
  };
}

/**
 * Add (or refresh) both Codex entries in the chosen Codex hooks.json.
 *
 * The one structural difference from Claude Code: event names sit at the ROOT
 * of hooks.json, not under a "hooks" key. Everything below that — the group
 * array, the command entries, the decision contract — is identical, which is
 * why the linter and the bounce flow are shared verbatim.
 *
 * The same holds for SessionStart (plan §8, resolved): Codex fires the event
 * with the same payload and honours the same `additionalContext` reply, so the
 * injector script installed here is byte-identical to Claude Code's, through
 * the same guard wrapper. Known gap: on Codex 0.130.0 a bare `codex` launch
 * that auto-restores the previous thread does not emit SessionStart
 * (openai/codex#24228). Nothing is injected then, the Stop backstop is
 * untouched, and the session simply behaves as it did before — fail-open, as
 * A26 requires.
 *
 * There is no audit-only counterpart. `notify` is a post-hoc observation
 * channel with no way to put text into the model's context, so a Codex below
 * CODEX_MIN_HOOK_VERSION gets no injector at all — the caller routes it to
 * installCodexNotify instead and never reaches this function.
 *
 * The command points at the shared core, wherever that ended up. On a
 * both-agents install that is the Claude Code root, by design (plan §7).
 *
 * `injector: false` wires the Stop hook alone, for `mode: both` — same reason
 * as on Claude Code, and the same removal of an injector already there. On
 * Codex the reason is stronger still: the block is in context on a resumed
 * thread that fires no SessionStart at all (openai/codex#24228), which is the
 * gap `both` exists to close.
 *
 * @param {object} [options]
 * @param {boolean} [options.injector=true] wire the SessionStart injector too
 * @returns {{path: string, command: string, sessionCommand: string|null, action: 'created'|'added'|'updated'|'unchanged', trustRequired: true}}
 */
function installCodexHook({ scope, cwd = process.cwd(), skillRoot, injector = true } = {}) {
  const root = skillRoot || getAdapter('codex').skillRoot();
  const file = codexHooksPath(scope, cwd);
  const { text, data, format } = readSettings(file);
  const command = codexHookCommand(root);
  const sessionCommand = injector ? sessionHookCommand(root) : null;

  const found = addHookEntry(data, command);
  let foundSession = true;
  if (injector) {
    foundSession = addHookEntry(data, sessionCommand, SESSION_EVENT);
  } else {
    removeHookEntry(data, SESSION_EVENT);
  }
  const next = writeSettings(file, data, format);

  return {
    path: file,
    command,
    sessionCommand,
    action: actionFor(text, next, found && foundSession),
    // Codex will not run the hook until the user grants trust once. The
    // installer cannot do it for them; it can only say so.
    trustRequired: true,
  };
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

  // Both events come out together. Leaving the injector behind after `unhook`
  // would keep writing style rules into a session nothing is enforcing, and the
  // A3 post-check greps for the tag in this file either way.
  const hooks = data.hooks;
  let removed = 0;
  if (hooks && typeof hooks === 'object' && !Array.isArray(hooks)) {
    removed = removeHookEntry(hooks) + removeHookEntry(hooks, SESSION_EVENT);
    if (Object.keys(hooks).length === 0) delete data.hooks;
  }

  if (removed === 0) return { path: file, removed: 0, action: 'absent' };
  writeSettings(file, data, format);
  return { path: file, removed, action: 'removed' };
}

// -------------------------------------------------- Codex notify fallback

// A TOML line assigning the top-level `notify` key. Anchored so a key called
// `notify_style` or a `notify` inside a table is never mistaken for it.
const NOTIFY_LINE = /^\s*notify\s*=/;
const SECTION_LINE = /^\s*\[/;

function notifyValue(skillRoot) {
  // Codex invokes the notify program with the event JSON appended as one extra
  // argument, so the value is a program plus its fixed leading args.
  return `notify = ["python3", ${JSON.stringify(codexNotifyScript(skillRoot))}]`;
}

function readToml(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
    return null;
  }
}

/**
 * Locate the top-level `notify` assignment, if there is one.
 *
 * Only the region before the first `[table]` header counts as top level, which
 * is what TOML scoping means and what Codex reads.
 */
function findNotify(lines) {
  for (let i = 0; i < lines.length; i += 1) {
    if (SECTION_LINE.test(lines[i])) return { index: -1, rootEnd: i };
    if (NOTIFY_LINE.test(lines[i])) return { index: i, rootEnd: -1 };
  }
  return { index: -1, rootEnd: lines.length };
}

/**
 * Point ~/.codex/config.toml `notify` at our audit script (plan A13).
 *
 * The edit is surgical by necessity: config.toml is hand-written by the user
 * and TOML has no marker-block convention, so every line we did not write is
 * copied through byte for byte. Only the single `notify` line is ours.
 *
 * `notify` is single-valued. If a foreign one is already there, taking it would
 * silently break whatever the user wired up, so we refuse and say what to do.
 * Refusing is recoverable; clobbering is not.
 *
 * @returns {{path: string, value: string, action: 'created'|'added'|'updated'|'unchanged'}}
 * @throws when a foreign notify program is already configured
 */
function installCodexNotify({ skillRoot } = {}) {
  const root = skillRoot || getAdapter('codex').skillRoot();
  const file = codexConfigPath();
  const text = readToml(file);
  const line = notifyValue(root);

  const hadTrailingNewline = text === null ? true : /\n$/.test(text);
  const body = text === null ? '' : (hadTrailingNewline ? text.slice(0, -1) : text);
  const lines = body === '' ? [] : body.split('\n');

  const { index, rootEnd } = findNotify(lines);
  if (index !== -1 && !lines[index].includes(TAG)) {
    throw new Error(
      `${file} already sets a notify program:\n` +
      `  ${lines[index].trim()}\n\n` +
      'Codex allows only one, and speakingwords will not overwrite yours. Either\n' +
      'remove that line and re-run, or fold the speakingwords call into your own\n' +
      `notify program:\n  ${codexNotifyScript(root)}`
    );
  }

  if (index !== -1) {
    lines[index] = line;
  } else {
    // Append at the end of the top-level region — after the last root key but
    // before the first [table], or at the end when there are no tables.
    lines.splice(rootEnd === -1 ? lines.length : rootEnd, 0, line);
  }

  const next = lines.join('\n') + (hadTrailingNewline ? '\n' : '');
  writeFileAtomic(file, next);

  return { path: file, value: line, action: actionFor(text, next, index !== -1) };
}

/** Remove our notify line, leaving every other line untouched. */
function uninstallCodexNotify() {
  const file = codexConfigPath();
  const text = readToml(file);
  if (text === null) return { path: file, removed: 0, action: 'missing' };

  const hadTrailingNewline = /\n$/.test(text);
  const body = hadTrailingNewline ? text.slice(0, -1) : text;
  const lines = body === '' ? [] : body.split('\n');

  const { index } = findNotify(lines);
  if (index === -1 || !lines[index].includes(TAG)) {
    return { path: file, removed: 0, action: 'absent' };
  }
  lines.splice(index, 1);

  // A config.toml that held nothing but our line goes entirely, rather than
  // being left behind as an empty file the user never asked for.
  if (lines.every((l) => l.trim() === '')) {
    fs.rmSync(file, { force: true });
    return { path: file, removed: 1, action: 'removed-file' };
  }

  writeFileAtomic(file, lines.join('\n') + (hadTrailingNewline ? '\n' : ''));
  return { path: file, removed: 1, action: 'removed' };
}

/**
 * Remove all Codex wiring: the hooks.json entries and the notify line alike.
 *
 * Both are cleared regardless of which one this machine installed, because a
 * Codex upgrade between install and uninstall would otherwise strand the other.
 * Stop and SessionStart come out together, for the same reason they do on
 * Claude Code: an injector left behind would keep stating rules that nothing is
 * enforcing, and the A3 post-check greps this file for the tag either way.
 */
function uninstallCodexHook({ scope, cwd = process.cwd() } = {}) {
  const file = codexHooksPath(scope, cwd);
  const { text, data, format } = readSettings(file);

  let hookAction = 'missing';
  let removed = 0;
  if (text !== null) {
    removed = removeHookEntry(data) + removeHookEntry(data, SESSION_EVENT);
    if (removed === 0) {
      hookAction = 'absent';
    } else if (Object.keys(data).length === 0) {
      // A hooks.json that held nothing but our entry goes with it.
      fs.rmSync(file, { force: true });
      hookAction = 'removed-file';
    } else {
      writeSettings(file, data, format);
      hookAction = 'removed';
    }
  }

  const notify = uninstallCodexNotify();
  return {
    hooks: { path: file, removed, action: hookAction },
    notify,
    removed: removed + notify.removed,
  };
}

// ------------------------------------------------------------ introspection

function hasOurEntry(events, event = EVENT) {
  const stop = events && events[event];
  if (!Array.isArray(stop)) return false;
  return stop.some((g) => g && Array.isArray(g.hooks) && g.hooks.some(isOurs));
}

/** Is our Claude Code Stop hook currently wired in this scope? */
function isInstalled({ scope, cwd = process.cwd() } = {}) {
  const { data } = readSettings(settingsPath(scope, cwd));
  return hasOurEntry(data.hooks);
}

/** Is our SessionStart injector currently wired in this scope? */
function isSessionInstalled({ scope, cwd = process.cwd() } = {}) {
  const { data } = readSettings(settingsPath(scope, cwd));
  return hasOurEntry(data.hooks, SESSION_EVENT);
}

/** Is our Codex Stop hook currently wired in this scope? */
function isCodexHookInstalled({ scope, cwd = process.cwd() } = {}) {
  const { data } = readSettings(codexHooksPath(scope, cwd));
  return hasOurEntry(data);
}

/** Is our SessionStart injector currently wired on Codex in this scope? */
function isCodexSessionInstalled({ scope, cwd = process.cwd() } = {}) {
  const { data } = readSettings(codexHooksPath(scope, cwd));
  return hasOurEntry(data, SESSION_EVENT);
}

/**
 * The foreign notify line already in config.toml, or null.
 *
 * Lets the installer refuse before it has written anything, rather than
 * failing halfway through and leaving one agent wired and the other not.
 */
function codexNotifyConflict() {
  const text = readToml(codexConfigPath());
  if (text === null) return null;
  const lines = text.split('\n');
  const { index } = findNotify(lines);
  if (index === -1 || lines[index].includes(TAG)) return null;
  return lines[index].trim();
}

/** Is our Codex notify fallback currently wired? */
function isCodexNotifyInstalled() {
  const text = readToml(codexConfigPath());
  if (text === null) return false;
  const lines = text.split('\n');
  const { index } = findNotify(lines);
  return index !== -1 && lines[index].includes(TAG);
}

module.exports = {
  TAG,
  EVENT,
  HOOK_SCRIPT,
  CODEX_HOOK_SCRIPT,
  CODEX_NOTIFY_SCRIPT,
  HOOK_GUARD_SCRIPT,
  SESSION_HOOK_SCRIPT,
  SESSION_EVENT,
  CODEX_MIN_HOOK_VERSION,
  settingsPath,
  codexHooksPath,
  codexConfigPath,
  codexNotifyScript,
  guardPath,
  hookCommand,
  sessionHookCommand,
  codexHookCommand,
  coreRoot,
  installSkillCore,
  detectCodexVersion,
  codexSupportsHooks,
  codexCapability,
  installClaudeHook,
  uninstallClaudeHook,
  installCodexHook,
  installCodexNotify,
  codexNotifyConflict,
  uninstallCodexNotify,
  uninstallCodexHook,
  isInstalled,
  isSessionInstalled,
  isCodexHookInstalled,
  isCodexSessionInstalled,
  isCodexNotifyInstalled,
};
