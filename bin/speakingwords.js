#!/usr/bin/env node
'use strict';

// speakingwords CLI — thin dispatcher.
//
// Phase 2 ships `init` in memory mode; phase 3 adds hook mode for Claude Code;
// phase 4 adds hook mode for Codex CLI, including the both-agents install;
// phase 5 adds the utils — `status`, `update`, `version` and `unhook`/`unset`;
// phase 7 moves help out to lib/help.js, so `help` is now a command like any
// other and the usage text has one owner.
// Every command's logic lives in lib/; this file only parses argv and picks
// one. No runtime dependencies anywhere.

const fs = require('node:fs');
const path = require('node:path');

const adapters = require('../lib/adapters');
const help = require('../lib/help');
const hooks = require('../lib/hooks');
const memory = require('../lib/memory');
const pref = require('../lib/pref');
const prompts = require('../lib/prompts');
const status = require('../lib/status');
const unhook = require('../lib/unhook');
const update = require('../lib/update');

const PKG_PATH = path.join(__dirname, '..', 'package.json');

function readVersion() {
  return JSON.parse(fs.readFileSync(PKG_PATH, 'utf8')).version;
}

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
    } else if (arg === '-y' || arg === '--yes') {
      // Boolean, so it must not swallow the next argument as its value.
      flags.yes = true;
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
    // A16/A18: a bad flag value is help shown *at* the user — overview to
    // stderr, exit 1, so a redirected `> file` never captures an error page.
    process.exit(help.run({
      reason: `--${name} must be one of: ${allowed.join(', ')} (got "${value}")`,
    }));
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

  // Validate every flag value before asking anything. A bad `--voice` must not
  // cost the user two answered questions before it complains, and the complaint
  // has to reach stderr with nothing on stdout (A16, A18).
  const givenMode = oneOf('mode', flags.mode, ['memory', 'hook']);
  const givenAgent = oneOf('agent', flags.agent, ['claude', 'codex', 'both']);
  const givenScope = oneOf('scope', flags.scope, ['local', 'global']);
  const givenVoice = oneOf('voice', flags.voice, ['terse', 'convo']);
  const givenConciseness = oneOf('conciseness', flags.conciseness, pref.LEVELS);

  // A command line that already answers mode, agent, scope and voice is a
  // script, not a conversation. Asking it a brand-new fourth question would
  // hang every 0.1.0 install script on a prompt it has no answer for, so a
  // fully specified invocation takes the default level instead of being asked.
  // Anything less than fully specified is an interactive run, and gets asked.
  const scripted = Boolean(givenMode && givenAgent && givenScope && givenVoice);

  // --- Question 1: mode ---
  let mode = givenMode;
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
  let agentSel = givenAgent;
  let scope = givenScope;

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
        const targets = agentsFor(candidate)
          .map((id) => (mode === 'hook'
            ? (id === 'claude'
              ? hooks.settingsPath(candidateScope, cwd)
              : hooks.codexHooksPath(candidateScope, cwd))
            : adapters.memoryTarget(id, candidateScope, cwd)))
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
  let voice = givenVoice;
  if (!voice) {
    voice = await prompts.choose('Which voice?', [
      { value: 'terse', label: 'terse', hint: 'point form only, brevity wins every trade-off' },
      { value: 'convo', label: 'convo', hint: 'prose retained, brevity not forced' },
    ], 'terse');
  }

  // --- Question 4: conciseness ---
  // Voice says what shape a reply takes; conciseness says how much of it
  // survives. They are independent axes, which is why they are two questions
  // and not one merged picker (plan §8).
  let conciseness = givenConciseness;
  if (!conciseness) {
    conciseness = scripted ? pref.DEFAULT_LEVEL : await prompts.choose('How concise?', [
      { value: 'low', label: 'low', hint: 'prose intact; only filler and restatement go' },
      { value: 'med', label: 'med', hint: 'every sentence earns its place; elaborations cut' },
      { value: 'high', label: 'high', hint: 'load-bearing content only; facts all still survive' },
    ], pref.DEFAULT_LEVEL);
  }

  // --- Write ---
  const agentIds = agentsFor(agentSel);

  if (mode === 'hook') {
    return installHookMode({ agentIds, scope, voice, conciseness, version, cwd });
  }

  const block = memory.renderBlock({ voice, conciseness, version });
  const results = agentIds.map((id) =>
    memory.writeBlock(adapters.memoryTarget(id, scope, cwd), block)
  );
  const prefFile = pref.writePref({ agents: agentIds, mode, scope, voice, conciseness, version });

  // --- Summary: say exactly what was written where ---
  const lines = [
    '',
    `speakingwords ${version} installed — memory mode, ${voice} voice, ${conciseness} conciseness.`,
    '',
  ];
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
// installed root, and one Stop-hook entry per agent. No memory block is
// written — hook mode enforces the rules, so restating them as suggestions in
// CLAUDE.md would only duplicate the contract in a second place.
//
// A both-agents install ships ONE core (plan §7) at the Claude Code root, and
// points the Codex wiring back at it. That is what makes a later `update` edit
// one lexicon and change behaviour on both agents at once.
function installHookMode({ agentIds, scope, voice, conciseness, version, cwd }) {
  const wantsCodex = agentIds.includes('codex');

  // Decide the Codex story before writing anything. If config.toml already has
  // someone else's notify program we must refuse, and refusing after wiring
  // Claude Code would leave a half-install behind.
  const codex = wantsCodex ? hooks.codexCapability() : null;
  if (codex && !codex.supportsHooks) {
    const conflict = hooks.codexNotifyConflict();
    if (conflict) {
      process.stderr.write(
        [
          `Codex ${codex.version} predates the stable hooks engine (v${codex.minVersion}), so`,
          'speakingwords would fall back to the config.toml `notify` audit path — but that',
          'key is already taken:',
          `  ${conflict}`,
          '',
          'Codex allows only one notify program, and yours will not be overwritten. Either',
          'upgrade Codex to get real hook enforcement, remove that line, or call',
          `${hooks.codexNotifyScript(hooks.coreRoot(agentIds))} from your own notify program.`,
          '',
        ].join('\n')
      );
      process.exit(1);
    }
  }

  const skillRoot = hooks.coreRoot(agentIds);
  hooks.installSkillCore(agentIds, skillRoot);

  const wirings = [];
  if (agentIds.includes('claude')) {
    wirings.push({ agent: 'claude', kind: 'hook', ...hooks.installClaudeHook({ scope, cwd, skillRoot }) });
  }
  if (wantsCodex) {
    wirings.push(codex.supportsHooks
      ? { agent: 'codex', kind: 'hook', ...hooks.installCodexHook({ scope, cwd, skillRoot }) }
      : { agent: 'codex', kind: 'notify', ...hooks.installCodexNotify({ skillRoot }) });
  }

  const prefFile = pref.writePref({
    agents: agentIds, mode: 'hook', scope, voice, conciseness, version,
  });

  // --- Summary: say exactly what was wired where, per agent ---
  const label = agentIds.map((id) => adapters.getAdapter(id).label).join(' + ');
  const lines = [
    '',
    `speakingwords ${version} installed — hook mode, ${voice} voice, `
    + `${conciseness} conciseness, ${label}.`,
    '',
  ];

  for (const wiring of wirings) {
    const name = adapters.getAdapter(wiring.agent).label;
    if (wiring.kind === 'hook') {
      lines.push(`  ${name}`);
      lines.push(`    hook entry   ${wiring.path}`);
      lines.push(`                 Stop → ${wiring.command}`);
      // Claude Code only. The injector states the rules up front so fewer
      // replies need bouncing; it can never block one.
      if (wiring.sessionCommand) {
        lines.push(`                 SessionStart → ${wiring.sessionCommand}`);
      }
    } else {
      lines.push(`  ${name}  (audit-only — see the downgrade note below)`);
      lines.push(`    notify       ${wiring.path}`);
      lines.push(`                 ${wiring.value}`);
    }
  }
  lines.push(`  skill root     ${skillRoot}`);
  lines.push(`  preferences    ${prefFile}`);
  lines.push('');

  if (agentIds.length > 1) {
    lines.push('  Both agents share one core at that skill root — one lexicon, one linter, one');
    lines.push('  hits.jsonl. Editing the rules once changes behaviour on both.');
    lines.push('');
  }

  // What actually happens next depends on whether anything can block. On an
  // all-audit install, describing a bounce would be a lie.
  const enforcing = wirings.filter((w) => w.kind === 'hook');
  if (enforcing.length > 0) {
    const only = enforcing.length < wirings.length
      ? ` on ${adapters.getAdapter(enforcing[0].agent).label}`
      : '';
    lines.push(`  Every reply is linted when the agent finishes${only}. A clean reply passes`);
    lines.push('  silently; a violating one is bounced once, rewritten against the voice');
    lines.push('  contract, and logged to hits.jsonl. One bounce maximum — the second pass');
    lines.push('  never blocks.');
  } else {
    lines.push('  Every reply is linted after the agent finishes, and every violation is logged');
    lines.push('  to hits.jsonl for `status` — but nothing is blocked or rewritten. This install');
    lines.push('  can only tell you what hook mode would have caught.');
  }
  lines.push('');

  if (agentIds.includes('claude')) {
    lines.push('  On Claude Code, the SessionStart hook also states the voice and conciseness');
    lines.push('  rules once at the top of each session, so fewer replies need bouncing at all.');
    lines.push('  It can never block a reply; if it fails or is masked, nothing changes.');
    lines.push('');
  }

  // --- Trust step: the one thing the installer cannot do for the user ---
  const codexHook = wirings.find((w) => w.agent === 'codex' && w.kind === 'hook');
  if (codexHook) {
    lines.push('  ONE STEP LEFT, on Codex only:');
    lines.push('    Codex will not run a hook until you trust it, once. On your next Codex run it');
    lines.push('    will ask; answer yes. You can also grant it up front with `/hooks` inside');
    lines.push('    Codex. Until you do, Codex replies pass unlinted — nothing is enforced.');
    lines.push('');
  }

  // --- A13: the downgrade must be stated plainly, not buried ---
  const codexNotify = wirings.find((w) => w.agent === 'codex' && w.kind === 'notify');
  if (codexNotify) {
    lines.push(`  DOWNGRADED on Codex: you are on ${codex.version}, and the hooks engine is only`);
    lines.push(`  stable from v${codex.minVersion}. There is no Stop hook to wire, so Codex got the`);
    lines.push('  audit-only fallback instead: replies are linted after the turn is already');
    lines.push('  delivered, and every violation is logged, but nothing can be blocked or');
    lines.push('  rewritten. Upgrade Codex and re-run init to get real enforcement.');
    if (scope === 'local') {
      lines.push('  Note: `notify` is user-level only in Codex — there is no per-project config to');
      lines.push('  write it to — so the audit pass applies everywhere, not just to this project.');
      if (agentIds.includes('claude')) {
        lines.push('  The local scope you chose still applies to Claude Code.');
      }
    }
    lines.push('');
  }

  if (wantsCodex && codex.version === null) {
    lines.push('  Could not read a Codex version (`codex --version` did not answer), so hook');
    lines.push('  wiring was written on the assumption it is current. If Codex ignores it, you');
    lines.push(`  are below v${codex.minVersion}; upgrade, or re-run with SPEAKINGWORDS_CODEX_VERSION set.`);
    lines.push('');
  }

  lines.push('  No memory block was written. Hook mode enforces the rules directly.');
  lines.push('');
  process.stdout.write(lines.join('\n'));
}

// ------------------------------------------------------------------ dispatch

async function main() {
  const { flags, positional } = parseArgs(process.argv.slice(2));
  const command = positional[0];

  if (flags.printVersion && !command) {
    process.stdout.write(`${readVersion()}\n`);
    return;
  }

  // Every help path is one call into lib/help — no command, `help`, `-h`,
  // `--help`, and `<command> --help` alike. Because the overview has exactly
  // one renderer, the three no-topic triggers are byte-identical by
  // construction rather than by test (A14).
  if (command === 'help') {
    process.exit(help.run({ topic: positional[1] }));
  }
  if (flags.help) {
    // `speakingwords init --help` is help the user asked for: the topic page
    // for that command, exit 0 (A16).
    process.exit(help.run({ topic: command }));
  }
  if (!command) {
    process.exit(help.run());
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
      process.exitCode = status.run();
      break;
    case 'update':
      // The hint is free text, so everything after the verb is the hint.
      process.exitCode = update.run(positional.slice(1).join(' '));
      break;
    case 'unhook':
    case 'unset':
      // `unset` is the alias, and behaves identically — same code path, so it
      // cannot drift.
      try {
        process.exitCode = await unhook.run({ yes: Boolean(flags.yes) });
      } finally {
        prompts.close();
      }
      break;
    default:
      process.exit(help.run({ reason: `Unknown command: ${command}` }));
  }
}

main().catch((err) => {
  process.stderr.write(`${err && err.message ? err.message : err}\n`);
  process.exit(1);
});
