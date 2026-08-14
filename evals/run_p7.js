#!/usr/bin/env node
'use strict';

// Deterministic assertion runner for Phase 7 (help becomes a util).
//
// No dependencies, no model calls, no network, no filesystem writes: help is a
// pure-text command, so every check here is "run the CLI, look at the bytes".
// SPEAKINGWORDS_HOME still points at a throwaway directory so that nothing can
// read or touch a real install by accident.
//
//   A14  `help`, no command at all, `-h` and `--help` produce byte-identical
//        overview output, and nothing the 0.1.0 usage text said was dropped.
//   A15  Every accepted command has a topic, and every topic maps back to a
//        real command. Checked in BOTH directions, against the dispatcher's own
//        `case` labels rather than a list copied into this file.
//   A16  Explicitly requested help exits 0; help shown after an unknown command
//        or a bad flag value exits 1.
//   A17  `help <unknown>` exits non-zero, prints the full overview, names the
//        bad topic, and never shows a stack trace.
//   A18  Success writes to stdout and nothing to stderr; errors write to stderr
//        and nothing to stdout, so `help > file` captures clean help only.
//
// Exit 0 if every assertion passes, 1 otherwise.

const { execFileSync, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const REPO = path.join(__dirname, '..');
const CLI = path.join(REPO, 'bin', 'speakingwords.js');
const DISPATCHER = path.join(REPO, 'bin', 'speakingwords.js');
const help = require(path.join(REPO, 'lib', 'help.js'));

// The exact usage text v0.1.0 shipped, pinned here so a future edit that
// silently drops a line from the overview fails this phase (A14). Read out of
// git rather than retyped: the point is fidelity to what users already saw.
//
// Read from the `v0.1.0` TAG, never from HEAD. This phase deletes the USAGE
// constant from the dispatcher, so the moment P7 is committed a HEAD-based
// lookup would find nothing and A14 would go red for a bookkeeping reason
// rather than a real regression. A tag is immutable — it keeps pointing at the
// text users actually saw, however far the branch moves on.
const USAGE_PIN_REF = 'v0.1.0:bin/speakingwords.js';
const V010_USAGE = readV010Usage();

const results = [];

function check(assertion, name, ok, detail = '') {
  results.push({ assertion, name, ok: Boolean(ok), detail });
}

function readV010Usage() {
  try {
    const src = execFileSync('git', ['show', USAGE_PIN_REF], {
      cwd: REPO,
      encoding: 'utf8',
    });
    const match = src.match(/const USAGE = `([\s\S]*?)`;/);
    return match ? match[1] : null;
  } catch {
    return null;
  }
}

const HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'speakingwords-p7-'));

function run(args) {
  const proc = spawnSync(process.execPath, [CLI, ...args], {
    cwd: HOME,
    encoding: 'utf8',
    env: { ...process.env, SPEAKINGWORDS_HOME: HOME },
  });
  return { code: proc.status, out: proc.stdout, err: proc.stderr };
}

// ------------------------------------------------------------------- A14

function evalOverviewIdentity() {
  const triggers = [[], ['help'], ['-h'], ['--help']];
  const outputs = triggers.map((args) => run(args));

  for (let i = 0; i < triggers.length; i += 1) {
    const label = triggers[i].length ? triggers[i].join(' ') : '(no args)';
    check('A14', `\`${label}\` exits 0`, outputs[i].code === 0, String(outputs[i].code));
    check('A14', `\`${label}\` writes nothing to stderr`, outputs[i].err === '', outputs[i].err);
  }

  const first = outputs[0].out;
  for (let i = 1; i < triggers.length; i += 1) {
    const label = triggers[i].join(' ');
    check(
      'A14',
      `\`${label}\` output is byte-identical to the no-args overview`,
      outputs[i].out === first,
      `${outputs[i].out.length} vs ${first.length} bytes`
    );
  }

  check('A14', 'the overview lists every command in the Usage block',
    ['init', 'version', 'status', 'update', 'unhook'].every((c) =>
      first.includes(`speakingwords ${c}`)), first);

  // Nothing from 0.1.0 may be dropped: every non-blank line of the old usage
  // text must still appear verbatim in the rendered overview.
  if (V010_USAGE === null) {
    check('A14', 'the 0.1.0 usage text is available to compare against', false,
      `could not read ${USAGE_PIN_REF}`);
  } else {
    const missing = V010_USAGE.split('\n')
      .filter((line) => line.trim())
      .filter((line) => !first.includes(line));
    check('A14', 'no line of the 0.1.0 overview was dropped', missing.length === 0,
      missing.join(' | '));
    check('A14', 'the overview is exactly the 0.1.0 text',
      first === `${V010_USAGE}\n`, 'overview text changed');
  }
}

// ------------------------------------------------------------------- A15

// The dispatcher's own accepted commands, scraped from its `case` labels plus
// the words that reach help directly. Scraping rather than restating is the
// whole point: if someone adds a command and forgets a topic, this fails.
function dispatcherCommands() {
  const src = fs.readFileSync(DISPATCHER, 'utf8');
  const switchBody = src.slice(src.indexOf('switch (command)'));
  const found = new Set(['help']);
  for (const match of switchBody.matchAll(/case '([a-z-]+)':/g)) found.add(match[1]);
  return [...found].sort();
}

function evalTopicCoverage() {
  const commands = dispatcherCommands();
  const topics = help.topics().sort();

  check('A15', 'the dispatcher exposes the commands we expect to find',
    commands.length >= 6, commands.join(', '));

  // Direction 1: every accepted command has a topic.
  const withoutTopic = commands.filter((c) => !help.findCommand(c));
  check('A15', 'every accepted command has a help topic', withoutTopic.length === 0,
    withoutTopic.join(', '));

  // Direction 2: every topic maps to a real command.
  const withoutCommand = topics.filter((t) => !commands.includes(t));
  check('A15', 'every help topic maps to a real command', withoutCommand.length === 0,
    withoutCommand.join(', '));

  // And the same both-directions check at the CLI boundary, not just in-process.
  for (const command of commands) {
    const proc = run(['help', command]);
    check('A15', `\`help ${command}\` prints a page and exits 0`,
      proc.code === 0 && proc.out.includes('Gotcha'), `${proc.code} ${proc.err.trim()}`);
    check('A15', `\`help ${command}\` names the command it documents`,
      proc.out.includes(`speakingwords ${help.findCommand(command).name} —`), proc.out);
  }
}

function evalTopicContent() {
  // Purpose, flags, and one gotcha — the three things a topic page owes (§2 W1).
  const init = run(['help', 'init']);
  check('P7', 'help init states the command purpose',
    init.out.includes('install a style contract'), init.out);
  check('P7', 'help init lists its flags',
    ['--memory', '--hook', '--agent', '--scope', '--voice'].every((f) => init.out.includes(f)),
    init.out);
  check('P7', 'help init carries exactly one Gotcha heading',
    (init.out.match(/^Gotcha$/gm) || []).length === 1, init.out);

  const update = run(['help', 'update']);
  check('P7', 'help update shows its hint examples',
    update.out.includes('speakingwords update "less emoji"'), update.out);
  check('P7', 'help update names the .bak discipline',
    update.out.includes('.bak'), update.out);

  const unhook = run(['help', 'unhook']);
  check('P7', 'help unhook shows its flag',
    unhook.out.includes('--yes'), unhook.out);

  const unset = run(['help', 'unset']);
  check('P7', 'the unset alias resolves to the unhook page',
    unset.out.includes('alias for `unhook`') && unset.out.includes('--yes'), unset.out);

  // One source of truth: the topic page reuses the overview's own lines rather
  // than a second copy that could drift (plan §8 "Topic depth").
  const overview = help.overview();
  const flagLine = overview
    .split('\n')
    .find((line) => line.includes('--agent claude|codex|both'));
  check('P7', 'the init topic page reuses the overview flag line verbatim',
    init.out.includes(flagLine), flagLine);

  for (const topic of help.topics()) {
    const page = help.topicPage(topic);
    check('P7', `the ${topic} page carries a gotcha`,
      page.includes('Gotcha') && page.split('Gotcha')[1].trim().length > 20, page);
  }
}

// -------------------------------------------------------------- A16, A17, A18

function evalExitCodes() {
  check('A16', '`help` exits 0', run(['help']).code === 0);
  check('A16', '`help init` exits 0', run(['help', 'init']).code === 0);
  check('A16', '`init --help` exits 0 (help the user asked for)',
    run(['init', '--help']).code === 0, String(run(['init', '--help']).code));

  const unknown = run(['bogus']);
  check('A16', 'an unknown command exits 1', unknown.code === 1, String(unknown.code));
  check('A16', 'an unknown command still shows the full overview',
    unknown.err.includes('Usage') && unknown.err.includes('init flags'), unknown.err);
  check('A16', 'an unknown command names itself', unknown.err.includes('bogus'), unknown.err);

  const badFlag = run(['init', '--voice', 'shouty']);
  check('A16', 'a bad flag value exits 1', badFlag.code === 1, String(badFlag.code));
  check('A16', 'a bad flag value says what the allowed values are',
    badFlag.err.includes('terse') && badFlag.err.includes('convo'), badFlag.err);
  check('A16', 'a bad flag value shows the overview too',
    badFlag.err.includes('Usage'), badFlag.err);
}

function evalUnknownTopic() {
  const proc = run(['help', 'frobnicate']);
  check('A17', 'an unknown topic exits non-zero', proc.code !== 0, String(proc.code));
  check('A17', 'an unknown topic names the topic typed',
    proc.err.includes('frobnicate'), proc.err);
  check('A17', 'an unknown topic prints the full overview',
    proc.err.includes(help.overview()), proc.err);
  check('A17', 'an unknown topic lists the real topics',
    help.topics().every((t) => proc.err.includes(t)), proc.err);
  check('A17', 'an unknown topic never shows a stack trace',
    !/^\s+at\s/m.test(proc.err) && !proc.err.includes('Error:'), proc.err);
}

function evalStreamSplit() {
  // A18: success is stdout-only, errors are stderr-only. Checked as a pair so
  // that neither stream can quietly pick up the other's traffic.
  for (const args of [[], ['help'], ['-h'], ['--help'], ['help', 'status'], ['init', '--help']]) {
    const label = args.length ? args.join(' ') : '(no args)';
    const proc = run(args);
    check('A18', `\`${label}\` writes help to stdout only`,
      proc.out.length > 0 && proc.err === '', proc.err);
  }

  for (const args of [['bogus'], ['help', 'frobnicate'], ['init', '--scope', 'sideways']]) {
    const proc = run(args);
    check('A18', `\`${args.join(' ')}\` writes the error page to stderr only`,
      proc.err.length > 0 && proc.out === '', proc.out);
  }
}

function evalDispatcherIsThin() {
  // The structural half of the phase: the dispatcher must not own help text.
  const src = fs.readFileSync(DISPATCHER, 'utf8');
  check('P7', 'the dispatcher no longer defines a USAGE constant',
    !src.includes('const USAGE'), 'USAGE still declared');
  check('P7', 'the dispatcher requires lib/help',
    src.includes("require('../lib/help')"), 'help not wired in');
  check('P7', 'help logic lives in lib/help.js',
    fs.existsSync(path.join(REPO, 'lib', 'help.js')), 'lib/help.js missing');
}

// -------------------------------------------------------------------- main

function main() {
  try {
    evalOverviewIdentity();
    evalTopicCoverage();
    evalTopicContent();
    evalExitCodes();
    evalUnknownTopic();
    evalStreamSplit();
    evalDispatcherIsThin();
  } finally {
    fs.rmSync(HOME, { recursive: true, force: true });
  }

  const grouped = new Map();
  for (const result of results) {
    if (!grouped.has(result.assertion)) grouped.set(result.assertion, []);
    grouped.get(result.assertion).push(result);
  }

  const out = ['', 'speakingwords — Phase 7 deterministic evals', ''];
  for (const assertion of [...grouped.keys()].sort()) {
    const group = grouped.get(assertion);
    const failed = group.filter((r) => !r.ok);
    out.push(
      `${assertion}  ${failed.length ? 'FAIL' : 'PASS'}  (${group.length - failed.length}/${group.length})`
    );
    for (const r of failed) out.push(`      FAILED: ${r.name}${r.detail ? ` — ${r.detail}` : ''}`);
  }

  const failures = results.filter((r) => !r.ok);
  out.push('');
  out.push(`${results.length - failures.length}/${results.length} checks passed`);
  out.push(failures.length ? 'PHASE 7 FAIL' : 'PHASE 7 PASS');
  out.push('');
  process.stdout.write(out.join('\n'));
  return failures.length ? 1 : 0;
}

process.exit(main());
