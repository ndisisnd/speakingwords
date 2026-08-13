#!/usr/bin/env node
'use strict';

// speakingwords CLI — thin dispatcher.
//
// Phase 2 ships `init` in memory mode; phase 3 adds hook mode for Claude Code.
// `status`, `update` and `unhook`/`unset` are declared here so the command
// surface is stable, but they exit 1 until their phase lands. No runtime
// dependencies anywhere.

const fs = require('node:fs');
const path = require('node:path');

const adapters = require('../lib/adapters');
const hooks = require('../lib/hooks');
const memory = require('../lib/memory');
const pref = require('../lib/pref');
const prompts = require('../lib/prompts');

const PKG_PATH = path.join(__dirname, '..', 'package.json');

function readVersion() {
  return JSON.parse(fs.readFileSync(PKG_PATH, 'utf8')).version;
}

const USAGE = `speakingwords — keep agent replies in the shape you asked for.

Usage
  speakingwords init [flags]     install a style contract (memory mode)
  speakingwords version          print the installed version
  speakingwords status           show what the linter caught          (phase 5)
  speakingwords update "<hint>"  tune the rules                        (phase 5)
  speakingwords unhook           remove hook wiring (alias: unset)     (phase 5)

init flags (skip the questions, for scripts and CI)
  --memory                       memory mode: write rules into the memory file
  --hook                         hook mode: lint every reply     (Claude Code)
  --agent claude|codex|both      which agent to install for
  --scope local|global           this project only, or everywhere
  --voice terse|convo            point form only, or prose retained
  -h, --help                     this text

Without flags, init asks three questions: mode, agent + scope, voice.`;

// ---------------------------------------------------------------- arg parsing

function parseArgs(argv) {
  const flags = {};
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '-h' || arg === '--help') {
      flags.help = true;
    } else if (arg === '-v' || arg === '--version') {
      flags.printVersion = true;
    } else if (arg === '--memory') {
      flags.mode = 'memory';
    } else if (arg === '--hook') {
      flags.mode = 'hook';
    } else if (arg.startsWith('--')) {
      const eq = arg.indexOf('=');
      const key = (eq === -1 ? arg.slice(2) : arg.slice(2, eq)).replace(/-/g, '');
      const value = eq === -1 ? argv[++i] : arg.slice(eq + 1);
      flags[key] = value;
    } else {
      positional.push(arg);
    }
  }
  return { flags, positional };
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function oneOf(name, value, allowed) {
  if (value === undefined) return undefined;
  if (!allowed.includes(value)) {
    fail(`--${name} must be one of: ${allowed.join(', ')} (got "${value}")`);
  }
  return value;
}

// ------------------------------------------------------------------ init flow

function agentsFor(selection) {
  return selection === 'both' ? ['claude', 'codex'] : [selection];
}

async function cmdInit(flags) {
  const version = readVersion();
  const cwd = process.cwd();

  // --- Question 1: mode ---
  let mode = oneOf('mode', flags.mode, ['memory', 'hook']);
  if (!mode) {
    mode = await prompts.choose('How should the style contract be enforced?', [
      {
        value: 'memory',
        label: 'memory',
        hint: 'writes up to 9 rule lines into the agent memory file; suggestive, not enforced',
      },
      {
        value: 'hook',
        label: 'hook',
        hint: 'lints every reply and bounces violations; stronger, larger footprint',
      },
    ], 'memory');
  }

  // --- Question 2: agent + scope, collapsed into one question to stay at 3 ---
  const detected = adapters.detectAgents();
  let agentSel = oneOf('agent', flags.agent, ['claude', 'codex', 'both']);
  let scope = oneOf('scope', flags.scope, ['local', 'global']);

  if (!agentSel || !scope) {
    if (detected.length === 0) {
      fail(
        'No supported agent found. speakingwords looks for ~/.claude (Claude Code) or\n' +
        '~/.codex (Codex CLI). Install one, or pass --agent and --scope explicitly.'
      );
    }

    const options = [];
    const candidates = detected.length === 2 ? ['claude', 'codex', 'both'] : detected;
    for (const candidate of candidates) {
      for (const candidateScope of ['local', 'global']) {
        const targets = mode === 'hook'
          ? (candidate === 'claude'
            ? hooks.settingsPath(candidateScope, cwd)
            : 'Codex hook wiring lands in phase 4')
          : agentsFor(candidate)
            .map((id) => adapters.memoryTarget(id, candidateScope, cwd))
            .join(' + ');
        const name = candidate === 'both'
          ? 'Both agents'
          : adapters.getAdapter(candidate).label;
        options.push({
          value: `${candidate}:${candidateScope}`,
          label: `${name} — ${candidateScope}`,
          hint: targets,
        });
      }
    }

    const picked = await prompts.choose(
      'Which agent, and this project only or everywhere?',
      options,
      options[0].value
    );
    const [pickedAgent, pickedScope] = picked.split(':');
    agentSel = agentSel || pickedAgent;
    scope = scope || pickedScope;
  }

  // --- Question 3: voice ---
  let voice = oneOf('voice', flags.voice, ['terse', 'convo']);
  if (!voice) {
    voice = await prompts.choose('Which voice?', [
      { value: 'terse', label: 'terse', hint: 'point form only, brevity wins every trade-off' },
      { value: 'convo', label: 'convo', hint: 'prose retained, brevity not forced' },
    ], 'terse');
  }

  // --- Write ---
  const agentIds = agentsFor(agentSel);

  if (mode === 'hook') {
    // Codex hook wiring (hooks.json, trust step, notify fallback) is phase 4.
    // Refuse loudly rather than half-install across two agents.
    if (agentIds.includes('codex')) {
      process.stderr.write(
        [
          'Hook mode is Claude Code only for now.',
          '',
          'The Codex adapter (hooks.json wiring, the trust step, and the notify audit',
          'fallback on versions below v0.124.0) lands in phase 4.',
          'Run `speakingwords init --hook --agent claude`, or use --memory for Codex.',
          '',
        ].join('\n')
      );
      process.exit(1);
    }
    return installHookMode({ scope, voice, version, cwd });
  }

  const block = memory.renderBlock({ voice, version });
  const results = agentIds.map((id) =>
    memory.writeBlock(adapters.memoryTarget(id, scope, cwd), block)
  );
  const prefFile = pref.writePref({ agents: agentIds, mode, scope, voice, version });

  // --- Summary: say exactly what was written where ---
  const lines = ['', `speakingwords ${version} installed — memory mode, ${voice} voice.`, ''];
  for (const result of results) {
    const verb = { created: 'created', replaced: 'updated block in', appended: 'added block to' }[result.action];
    lines.push(`  ${verb} ${result.path}`);
  }
  lines.push(`  preferences  ${prefFile}`);
  lines.push('');
  lines.push(`  ${memory.countBullets(block)} rule lines, inside <!-- speakingwords:start --> markers.`);
  lines.push('  Nothing outside those markers was touched; re-running init replaces the block in place.');
  lines.push('');
  lines.push('  Memory mode is suggestive, not enforced — the agent can still drift.');
  lines.push('  Hook mode enforces the same rules on every reply (phase 3).');
  lines.push('');
  process.stdout.write(lines.join('\n'));
}

// Hook mode installs two things and nothing else: the skill core at the
// installed root, and one Stop-hook entry in settings.json. No memory block is
// written — hook mode enforces the rules, so restating them as suggestions in
// CLAUDE.md would only duplicate the contract in a second place.
function installHookMode({ scope, voice, version, cwd }) {
  const { skillRoot } = hooks.installSkillCore('claude');
  const result = hooks.installClaudeHook({ scope, cwd, skillRoot });
  const prefFile = pref.writePref({ agents: ['claude'], mode: 'hook', scope, voice, version });

  const lines = ['', `speakingwords ${version} installed — hook mode, ${voice} voice.`, ''];
  lines.push(`  hook entry   ${result.path}`);
  lines.push(`               Stop → ${result.command}`);
  lines.push(`  skill root   ${skillRoot}`);
  lines.push(`  preferences  ${prefFile}`);
  lines.push('');
  lines.push('  Every reply is linted when the agent finishes. A clean reply passes silently;');
  lines.push('  a violating one is bounced once, rewritten against the voice contract, and');
  lines.push('  logged to hits.jsonl. One bounce maximum — the second pass never blocks.');
  lines.push('');
  lines.push('  No memory block was written. Hook mode enforces the rules directly.');
  lines.push('');
  process.stdout.write(lines.join('\n'));
}

// ------------------------------------------------------------------ dispatch

function notYet(command, phase) {
  process.stderr.write(`\`speakingwords ${command}\` is not yet implemented (phase ${phase}).\n`);
  process.exit(1);
}

async function main() {
  const { flags, positional } = parseArgs(process.argv.slice(2));
  const command = positional[0];

  if (flags.printVersion && !command) {
    process.stdout.write(`${readVersion()}\n`);
    return;
  }

  if (flags.help || !command || command === 'help') {
    process.stdout.write(`${USAGE}\n`);
    process.exit(command && command !== 'help' ? 1 : 0);
  }

  switch (command) {
    case 'init':
      try {
        await cmdInit(flags);
      } finally {
        // Release stdin, otherwise the process hangs on the open interface.
        prompts.close();
      }
      break;
    case 'version':
      process.stdout.write(`${readVersion()}\n`);
      break;
    case 'status':
      notYet('status', 5);
      break;
    case 'update':
      notYet('update', 5);
      break;
    case 'unhook':
      notYet('unhook', 5);
      break;
    case 'unset':
      notYet('unset', 5);
      break;
    default:
      process.stderr.write(`Unknown command: ${command}\n\n${USAGE}\n`);
      process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`${err && err.message ? err.message : err}\n`);
  process.exit(1);
});
