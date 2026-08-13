'use strict';

// `speakingwords unhook` (alias `unset`) — take the enforcement back out
// (plan §5, assertion A3).
//
// The shape of the command is deliberate. Removing a hook changes what happens
// on every future reply, so the user is told exactly which files will be
// touched *before* anything is, and a bare Enter means no. `--yes` exists for
// scripts and CI, and skips only the question, not the report.
//
// What comes out: every hook wiring recorded in pref.json — the Claude Code
// settings.json entry, the Codex hooks.json entry, and the Codex config.toml
// notify line. What stays: hits.jsonl and the installed skill files. The
// telemetry is the user's record of what was caught, and deleting their history
// is not part of turning enforcement off. Reinstalling is `init` again, and
// pref.json keeps the agents and voice so that install has nothing to re-ask.
//
// A3 is checked by the command itself, not by a test: after removal it greps
// every config file it touched for "speakingwords" and prints the count. A
// non-zero count is a loud failure and a non-zero exit, because a half-removed
// hook is worse than either state.

const fs = require('node:fs');

const adapters = require('./adapters');
const hooks = require('./hooks');
const pref = require('./pref');
const prompts = require('./prompts');

const TAG = 'speakingwords';

/** Every config file this install could have written, for the post-check. */
function configFiles(agents, scope, cwd) {
  const files = [];
  if (agents.includes('claude')) files.push(hooks.settingsPath(scope, cwd));
  if (agents.includes('codex')) {
    files.push(hooks.codexHooksPath(scope, cwd));
    files.push(hooks.codexConfigPath());
  }
  return files;
}

/**
 * A3 post-check: no reference to us may survive in any file we touched.
 *
 * @returns {{checked: string[], offenders: Array<{file:string, count:number}>}}
 */
function postCheck(files) {
  const checked = [];
  const offenders = [];
  for (const file of files) {
    let text;
    try {
      text = fs.readFileSync(file, 'utf8');
    } catch (err) {
      if (err.code === 'ENOENT') continue; // a file that is gone holds nothing
      throw err;
    }
    checked.push(file);
    const count = text.split(TAG).length - 1;
    if (count > 0) offenders.push({ file, count });
  }
  return { checked, offenders };
}

function describeRemoval(result) {
  const out = [];
  if (result.kind === 'claude') {
    const map = {
      removed: `removed the Stop hook entry from ${result.path}`,
      absent: `no entry of ours was in ${result.path}`,
      missing: `${result.path} does not exist`,
    };
    out.push(`  ${map[result.action]}`);
  } else {
    const h = result.hooks;
    const n = result.notify;
    const hookMap = {
      removed: `removed the Stop hook entry from ${h.path}`,
      'removed-file': `removed ${h.path} (it held nothing else)`,
      absent: `no entry of ours was in ${h.path}`,
      missing: `${h.path} does not exist`,
    };
    out.push(`  ${hookMap[h.action]}`);
    const notifyMap = {
      removed: `removed the notify line from ${n.path}`,
      'removed-file': `removed ${n.path} (it held nothing else)`,
      absent: `no notify line of ours was in ${n.path}`,
      missing: `${n.path} does not exist`,
    };
    out.push(`  ${notifyMap[n.action]}`);
  }
  return out;
}

/**
 * @param {object} [options]
 * @param {boolean} [options.yes]  skip the confirmation (scripts, CI)
 * @returns {Promise<number>} exit code
 */
async function run(options = {}) {
  const write = options.write || ((text) => process.stdout.write(text));
  const writeErr = options.writeErr || ((text) => process.stderr.write(text));
  const cwd = options.cwd || process.cwd();
  const found = pref.findPref();

  if (!found) {
    writeErr(
      '\nNo speakingwords install found, so there is no hook wiring to remove.\n' +
      'Nothing changed.\n\n'
    );
    return 0;
  }

  const settings = found.pref || {};
  const agents = Array.isArray(settings.agents) && settings.agents.length
    ? settings.agents
    : ['claude'];
  const scope = settings.scope || 'global';

  if (settings.mode === 'unhooked') {
    write(
      '\nThis install is already unhooked — no hook wiring is in place.\n' +
      `Your hit history is still at ${found.path.replace(/pref\.json$/, 'hits.jsonl')}.\n` +
      'Re-run `speakingwords init --hook` to turn enforcement back on.\n\n'
    );
    return 0;
  }

  // Memory mode never installed a hook, so there is nothing here to undo. The
  // honest answer is to say so and point at the block that *is* installed.
  if (settings.mode !== 'hook') {
    const lines = [
      '',
      'This install is in memory mode, and `unhook` only removes hook wiring.',
      'There is no hook, no settings entry and no telemetry to take out.',
      '',
      'What memory mode did install is a marker block in:',
    ];
    for (const agent of agents) {
      lines.push(`  ${adapters.memoryTarget(agent, scope, cwd)}`);
    }
    lines.push('');
    lines.push('To remove it, delete everything from <!-- speakingwords:start --> to');
    lines.push('<!-- speakingwords:end --> in those files. Nothing outside the markers is ours.');
    lines.push('');
    write(lines.join('\n'));
    return 0;
  }

  // --- Warn first: exactly what will be removed, and what will not ---
  const files = configFiles(agents, scope, cwd);
  const warn = ['', 'This removes speakingwords hook enforcement. To be removed:', ''];
  for (const agent of agents) {
    const label = adapters.getAdapter(agent).label;
    if (agent === 'claude') {
      warn.push(`  ${label}`);
      warn.push(`    Stop hook entry in ${hooks.settingsPath(scope, cwd)}`);
    } else {
      warn.push(`  ${label}`);
      warn.push(`    Stop hook entry in ${hooks.codexHooksPath(scope, cwd)}`);
      warn.push(`    notify line in    ${hooks.codexConfigPath()}`);
    }
  }
  warn.push('');
  warn.push('  After this, replies are no longer linted and nothing is bounced.');
  warn.push('');
  warn.push('  Kept: your telemetry. hits.jsonl and the installed skill files stay where');
  warn.push('  they are, so `status` still shows everything caught so far.');
  warn.push('');
  write(warn.join('\n'));

  if (!options.yes) {
    let ok = false;
    try {
      ok = await prompts.confirm('Continue?', false);
    } catch {
      // No stdin to answer with (a pipe that closed, a non-interactive shell).
      // An unanswered question is not consent, so it reads as no. `--yes` is
      // the way to remove the wiring without a terminal.
      ok = false;
    }
    if (!ok) {
      write('\nNothing changed.\n\n');
      return 0;
    }
  }

  // --- Remove ---
  const removals = [];
  if (agents.includes('claude')) {
    removals.push({ kind: 'claude', ...hooks.uninstallClaudeHook({ scope, cwd }) });
  }
  if (agents.includes('codex')) {
    removals.push({ kind: 'codex', ...hooks.uninstallCodexHook({ scope, cwd }) });
  }

  // Mode becomes "unhooked" rather than being cleared: agents, scope and voice
  // stay, so a later `init` has nothing to re-ask and `status` still explains
  // itself correctly.
  pref.writePref({
    agents,
    mode: 'unhooked',
    scope,
    voice: settings.voice || null,
    version: settings.version || null,
  });

  const out = ['', 'Removed:', ''];
  for (const removal of removals) out.push(...describeRemoval(removal));
  out.push('');
  out.push(`  pref.json mode is now "unhooked" (${found.path})`);
  out.push('  hits.jsonl and the installed skill files were kept.');
  out.push('');

  // --- A3: prove it, in the command itself ---
  const { checked, offenders } = postCheck(files);
  const total = offenders.reduce((sum, o) => sum + o.count, 0);
  if (offenders.length === 0) {
    out.push(`  post-check: 0 references remain (${checked.length} config file${checked.length === 1 ? '' : 's'} checked).`);
    out.push('');
    write(out.join('\n'));
    return 0;
  }

  out.push(`  POST-CHECK FAILED: ${total} "${TAG}" reference${total === 1 ? '' : 's'} still present.`);
  for (const offender of offenders) {
    out.push(`    ${offender.file}  (${offender.count})`);
  }
  out.push('');
  out.push('  Enforcement may still fire. Remove those lines by hand, or re-run unhook.');
  out.push('');
  writeErr(out.join('\n'));
  return 1;
}

module.exports = { run, postCheck, configFiles };
