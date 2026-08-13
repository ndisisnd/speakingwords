#!/usr/bin/env node
'use strict';

// Deterministic assertion runner for Phase 2 (CLI scaffold + memory mode).
// No dependencies, no model calls, no network. Everything happens inside a
// throwaway temp tree: SPEAKINGWORDS_HOME fakes the home directory and the
// child process cwd fakes the project, so the real ~/.claude and ~/.codex are
// never touched.
//
//   A1   The rendered block is at most 9 bullet lines, in both voices, and the
//        renderer refuses (throws) when handed an oversized template.
//   A2   Memory writes are idempotent on the Claude targets: install -> install
//        produces a byte-identical file, whether or not the block already
//        existed, and user content outside the markers survives untouched.
//   A12  The same guarantee on the Codex targets (AGENTS.md, local and global).
//
// Exit 0 when every assertion passes, 1 otherwise.

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const REPO = path.join(__dirname, '..');
const CLI = path.join(REPO, 'bin', 'speakingwords.js');
const memory = require(path.join(REPO, 'lib', 'memory.js'));

const results = [];

function check(assertion, name, ok, detail = '') {
  results.push({ assertion, name, ok: Boolean(ok), detail });
}

function mkTemp(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `speakingwords-${label}-`));
}

function runInit(home, cwd, args) {
  return execFileSync(process.execPath, [CLI, 'init', ...args], {
    cwd,
    env: { ...process.env, SPEAKINGWORDS_HOME: home },
    encoding: 'utf8',
    stdio: 'pipe', // keep child stderr out of the runner's own report
  });
}

// --------------------------------------------------------------------- A1

function evalA1() {
  for (const voice of ['terse', 'convo']) {
    let block;
    let threw = null;
    try {
      block = memory.renderBlock({ voice });
    } catch (err) {
      threw = err;
    }
    if (threw) {
      check('A1', `${voice} block renders`, false, threw.message);
      continue;
    }
    const count = memory.countBullets(block);
    check('A1', `${voice} block <= 9 bullet lines`, count <= 9, `${count} bullet lines`);
    check('A1', `${voice} block is marker-delimited`, block.startsWith(memory.START) && block.endsWith(memory.END));
    check('A1', `${voice} block names its voice`, block.includes(`voice: ${voice}`));
  }

  // Injected oversized template: the renderer must refuse rather than truncate.
  const oversized = Array.from({ length: 10 }, (_, i) => `Injected oversized rule line ${i + 1}.`);
  let refused = false;
  let message = '';
  try {
    memory.renderBlock({ voice: 'terse', bullets: oversized });
  } catch (err) {
    refused = true;
    message = err.message;
  }
  check('A1', 'renderer throws on a 10-line template', refused, refused ? message : 'no error thrown');

  // A 9-line template is exactly at budget and must still render.
  let atBudget = true;
  try {
    memory.renderBlock({ voice: 'terse', bullets: oversized.slice(0, 9) });
  } catch (err) {
    atBudget = false;
    message = err.message;
  }
  check('A1', 'renderer accepts a 9-line template', atBudget, atBudget ? '' : message);
}

// ---------------------------------------------------------------- A2 / A12

const PRE_EXISTING = [
  '# Project notes',
  '',
  'Keep the migration order in mind before touching the schema.',
  '',
  '## Conventions',
  '',
  '- Tests live beside the code.',
  '',
].join('\n');

// One case = one agent/scope target, run twice, with and without prior content.
function idempotencyCase(assertion, agent, scope, voice, seedExisting) {
  const home = mkTemp('home');
  const project = mkTemp('project');
  // Make both agents "present" so detection never blocks the non-interactive path.
  fs.mkdirSync(path.join(home, '.claude'), { recursive: true });
  fs.mkdirSync(path.join(home, '.codex'), { recursive: true });

  const target = scope === 'global'
    ? path.join(home, agent === 'claude' ? '.claude/CLAUDE.md' : '.codex/AGENTS.md')
    : path.join(project, agent === 'claude' ? 'CLAUDE.local.md' : 'AGENTS.md');

  if (seedExisting) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, PRE_EXISTING, 'utf8');
  }

  const label = `${agent} ${scope}${seedExisting ? ' (existing file)' : ' (fresh file)'}`;
  const args = ['--memory', '--agent', agent, '--scope', scope, '--voice', voice];

  try {
    runInit(home, project, args);
  } catch (err) {
    check(assertion, `${label}: first install`, false, String(err.stderr || err.message).trim());
    return;
  }
  const first = fs.readFileSync(target, 'utf8');

  try {
    runInit(home, project, args);
  } catch (err) {
    check(assertion, `${label}: second install`, false, String(err.stderr || err.message).trim());
    return;
  }
  const second = fs.readFileSync(target, 'utf8');

  check(assertion, `${label}: install -> install is byte identical`, first === second,
    first === second ? '' : 'second run changed the file');
  check(assertion, `${label}: exactly one marker block`,
    (first.match(/<!-- speakingwords:start -->/g) || []).length === 1);
  check(assertion, `${label}: block <= 9 bullet lines on disk`,
    memory.countBullets(first.match(memory.BLOCK_RE)[0]) <= 9);

  if (seedExisting) {
    const outside = first.replace(memory.BLOCK_RE, '').replace(/\s+$/, '');
    check(assertion, `${label}: user content outside the markers survives`,
      outside === PRE_EXISTING.replace(/\s+$/, ''));
  }

  // pref.json records what happened, at the right root.
  const prefFile = agent === 'claude'
    ? path.join(home, '.claude/skills/speakingwords/pref.json')
    : path.join(home, '.codex/speakingwords/pref.json');
  let pref = null;
  try {
    pref = JSON.parse(fs.readFileSync(prefFile, 'utf8'));
  } catch { /* reported below */ }
  check(assertion, `${label}: pref.json written`, Boolean(pref), prefFile);
  if (pref) {
    check(assertion, `${label}: pref.json matches the install`,
      pref.mode === 'memory' && pref.scope === scope && pref.voice === voice
      && Array.isArray(pref.agents) && pref.agents.includes(agent));
  }

  fs.rmSync(home, { recursive: true, force: true });
  fs.rmSync(project, { recursive: true, force: true });
}

function evalIdempotency() {
  for (const seed of [false, true]) {
    idempotencyCase('A2', 'claude', 'local', 'terse', seed);
    idempotencyCase('A2', 'claude', 'global', 'convo', seed);
    idempotencyCase('A12', 'codex', 'local', 'terse', seed);
    idempotencyCase('A12', 'codex', 'global', 'convo', seed);
  }

  // Both agents at once: one core, two targets, still idempotent.
  const home = mkTemp('home');
  const project = mkTemp('project');
  fs.mkdirSync(path.join(home, '.claude'), { recursive: true });
  fs.mkdirSync(path.join(home, '.codex'), { recursive: true });
  const args = ['--memory', '--agent', 'both', '--scope', 'local', '--voice', 'terse'];
  runInit(home, project, args);
  const firstClaude = fs.readFileSync(path.join(project, 'CLAUDE.local.md'), 'utf8');
  const firstCodex = fs.readFileSync(path.join(project, 'AGENTS.md'), 'utf8');
  runInit(home, project, args);
  const secondClaude = fs.readFileSync(path.join(project, 'CLAUDE.local.md'), 'utf8');
  const secondCodex = fs.readFileSync(path.join(project, 'AGENTS.md'), 'utf8');
  check('A2', 'both agents: claude target idempotent', firstClaude === secondClaude);
  check('A12', 'both agents: codex target idempotent', firstCodex === secondCodex);
  check('A11', 'both agents: identical block in both targets',
    firstClaude.match(memory.BLOCK_RE)[0] === firstCodex.match(memory.BLOCK_RE)[0]);
  const bothPref = JSON.parse(
    fs.readFileSync(path.join(home, '.claude/skills/speakingwords/pref.json'), 'utf8')
  );
  check('A2', 'both agents: pref.json lists both', bothPref.agents.join(',') === 'claude,codex');
  fs.rmSync(home, { recursive: true, force: true });
  fs.rmSync(project, { recursive: true, force: true });
}

// ------------------------------------------------------------ CLI surface

function evalCli() {
  const pkgVersion = JSON.parse(fs.readFileSync(path.join(REPO, 'package.json'), 'utf8')).version;
  const printed = execFileSync(process.execPath, [CLI, 'version'], { encoding: 'utf8' }).trim();
  check('A7', 'version prints the package.json version', printed === pkgVersion, `${printed} vs ${pkgVersion}`);

  const help = execFileSync(process.execPath, [CLI, '--help'], { encoding: 'utf8' });
  check('P2', '--help lists every command',
    ['init', 'version', 'status', 'update', 'unhook'].every((c) => help.includes(c)));

  for (const [command, phase] of [['status', 5], ['update', 5], ['unhook', 5], ['unset', 5]]) {
    let code = 0;
    let stderr = '';
    try {
      execFileSync(process.execPath, [CLI, command], { encoding: 'utf8', stdio: 'pipe' });
    } catch (err) {
      code = err.status;
      stderr = String(err.stderr || '');
    }
    check('P2', `${command} stub exits 1 and names its phase`,
      code === 1 && stderr.includes(`phase ${phase}`), stderr.trim());
  }

  // Interactive path: three questions, answered over a pipe. This guards the
  // buffered-stdin regression where later answers were dropped and the flow
  // died on a false EOF.
  {
    const home = mkTemp('home');
    const project = mkTemp('project');
    fs.mkdirSync(path.join(home, '.claude'), { recursive: true });
    fs.mkdirSync(path.join(home, '.codex'), { recursive: true });
    let out = '';
    let err = '';
    try {
      out = execFileSync(process.execPath, [CLI, 'init'], {
        cwd: project,
        env: { ...process.env, SPEAKINGWORDS_HOME: home },
        input: '1\n5\n2\n', // memory -> both agents, local -> convo
        encoding: 'utf8',
        stdio: 'pipe',
      });
    } catch (e) {
      err = String(e.stderr || e.message);
    }
    const prompted = (out.match(/Choose 1-/g) || []).length;
    check('P2', 'interactive init asks exactly 3 questions', prompted === 3, err || `${prompted} prompts`);
    check('P2', 'interactive init writes both targets',
      fs.existsSync(path.join(project, 'CLAUDE.local.md')) && fs.existsSync(path.join(project, 'AGENTS.md')), err);
    check('P2', 'interactive init honours the picked voice', out.includes('convo voice'), err);
    fs.rmSync(home, { recursive: true, force: true });
    fs.rmSync(project, { recursive: true, force: true });
  }

  // Hook mode must refuse loudly rather than half-install.
  const home = mkTemp('home');
  const project = mkTemp('project');
  fs.mkdirSync(path.join(home, '.claude'), { recursive: true });
  let hookCode = 0;
  let hookErr = '';
  try {
    runInit(home, project, ['--hook', '--agent', 'claude', '--scope', 'local', '--voice', 'terse']);
  } catch (err) {
    hookCode = err.status;
    hookErr = String(err.stderr || '');
  }
  check('P2', 'hook mode exits 1 and says which phase installs it',
    hookCode === 1 && /phase 3/.test(hookErr));
  check('P2', 'hook mode wrote nothing',
    !fs.existsSync(path.join(project, 'CLAUDE.local.md')));
  fs.rmSync(home, { recursive: true, force: true });
  fs.rmSync(project, { recursive: true, force: true });
}

// -------------------------------------------------------------------- main

function main() {
  evalA1();
  evalIdempotency();
  evalCli();

  const byAssertion = new Map();
  for (const r of results) {
    if (!byAssertion.has(r.assertion)) byAssertion.set(r.assertion, []);
    byAssertion.get(r.assertion).push(r);
  }

  const out = ['', 'speakingwords — Phase 2 assertions', ''];
  for (const [assertion, group] of byAssertion) {
    const failed = group.filter((r) => !r.ok);
    out.push(`${assertion}  ${failed.length === 0 ? 'PASS' : 'FAIL'}  (${group.length - failed.length}/${group.length} checks)`);
    for (const r of failed) {
      out.push(`     FAILED: ${r.name}${r.detail ? ` — ${r.detail}` : ''}`);
    }
  }

  const failures = results.filter((r) => !r.ok);
  out.push('');
  out.push(`${results.length - failures.length}/${results.length} checks passed.`);
  out.push(failures.length === 0 ? 'Phase 2 gate: PASS' : 'Phase 2 gate: FAIL');
  out.push('');
  process.stdout.write(out.join('\n'));
  process.exit(failures.length === 0 ? 0 : 1);
}

main();
