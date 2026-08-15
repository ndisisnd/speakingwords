'use strict';

// `speakingwords status` — what the linter actually caught (plan §5).
//
// Hook mode is the only mode that produces telemetry, so this command has two
// jobs: render the hit table when there is one, and explain plainly why there
// is nothing to render when there is not. Neither path is allowed to fail:
//
//   A9   hits.jsonl lines are single-line JSON. A malformed line is skipped and
//        counted, never thrown. A half-written line from a killed process must
//        not cost the user the rest of their history.
//   A10  Memory mode exits 0 with one explanatory line, never a stack trace.
//
// The log is append-only and shared by every agent in the install, so the table
// is a straight aggregation by rule id: how often it fired, one real example of
// what tripped it, and when it last happened.

const fs = require('node:fs');
const path = require('node:path');

const adapters = require('./adapters');
const hooks = require('./hooks');
const memory = require('./memory');
const pref = require('./pref');

const HITS_FILE = 'hits.jsonl';
// The hook rotates the log at 1 MB into one previous generation (A23). Reading
// both is what keeps the totals steady across a rotation: the same history is
// counted, it just lives in two files for a while.
const ROTATED_FILE = 'hits.jsonl.1';
const EXAMPLE_WIDTH = 44;

// Said the same way in both no-telemetry paths, because it is the same fact:
// memory mode never installs the thing that does the counting.
const MEMORY_LINE =
  'Memory mode installs no hook, so there is nothing to count — hook mode records every catch.';

// ------------------------------------------------------------------- timing

function humanizeAgo(ms) {
  if (!Number.isFinite(ms)) return 'unknown';
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  return `${Math.floor(s / 2592000)}mo ago`;
}

function humanizeSpan(ms) {
  if (!Number.isFinite(ms) || ms < 0) return 'unknown';
  const s = Math.round(ms / 1000);
  if (s < 60) return 'under a minute';
  if (s < 3600) return `${Math.max(1, Math.floor(s / 60))}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function parseTs(value) {
  if (typeof value !== 'string') return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

// --------------------------------------------------------------- log reading

/**
 * Read and aggregate hits.jsonl.
 *
 * A line counts as malformed when it is not JSON, is not an object, or carries
 * no rule id — all three are equally useless to the table, and all three are
 * skipped rather than fatal (A9).
 */
function readHits(file) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return { records: [], malformed: 0, missing: true };
    throw err;
  }

  const records = [];
  let malformed = 0;
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      malformed += 1;
      continue;
    }
    if (!record || typeof record !== 'object' || Array.isArray(record)) {
      malformed += 1;
      continue;
    }
    if (typeof record.rule !== 'string' || !record.rule) {
      malformed += 1;
      continue;
    }
    records.push(record);
  }
  return { records, malformed, missing: false };
}

/**
 * Read the whole retained history: the rotated generation first, then the live
 * log, so records stay in the order they were written.
 *
 * `missing` means there is no telemetry at all — one file being absent is the
 * normal state before the first rotation, not a gap.
 */
function readAllHits(skillRoot) {
  const older = readHits(path.join(skillRoot, ROTATED_FILE));
  const current = readHits(path.join(skillRoot, HITS_FILE));
  return {
    records: older.records.concat(current.records),
    malformed: older.malformed + current.malformed,
    missing: older.missing && current.missing,
    rotated: !older.missing,
  };
}

function aggregate(records, now) {
  const byRule = new Map();
  for (const record of records) {
    let row = byRule.get(record.rule);
    if (!row) {
      row = { rule: record.rule, count: 0, severity: '', example: '', lastMs: null, lastRaw: '' };
      byRule.set(record.rule, row);
    }
    row.count += 1;
    if (!row.example && typeof record.match === 'string' && record.match.trim()) {
      row.example = record.match.trim();
    }
    if (!row.severity && typeof record.severity === 'string') row.severity = record.severity;
    const ms = parseTs(record.ts);
    if (ms !== null && (row.lastMs === null || ms > row.lastMs)) {
      row.lastMs = ms;
      row.lastRaw = record.ts;
    }
  }

  const rows = [...byRule.values()];
  // Loudest rule first; ties resolved by rule id so the table is stable.
  rows.sort((a, b) => (b.count - a.count) || a.rule.localeCompare(b.rule));
  for (const row of rows) {
    row.lastSeen = row.lastMs === null ? 'unknown' : humanizeAgo(now - row.lastMs);
    if (!row.severity) row.severity = 'warn';
    if (!row.example) row.example = '—';
    if (row.example.length > EXAMPLE_WIDTH) {
      row.example = `${row.example.slice(0, EXAMPLE_WIDTH - 1)}…`;
    }
  }
  return rows;
}

// ------------------------------------------------------- degraded install

// Left behind by scripts/hook_guard.sh when it could not find python3 (A24).
const DISABLED_FILE = 'lint_disabled';
const DISABLED_KEY = 'lint_disabled_reason';

function python3OnPath() {
  const dirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const dir of dirs) {
    try {
      fs.accessSync(path.join(dir, 'python3'), fs.constants.X_OK);
      return true;
    } catch { /* keep looking */ }
  }
  return false;
}

/**
 * Why linting is off, or null when it is not.
 *
 * Two sources, in order of evidence. The note the hook wrapper left is the
 * strong one — it means the hook actually ran and found nothing to run with.
 * A live PATH probe is the weak one, but it catches the case where hook mode is
 * installed on a machine that never had the interpreter, before a single reply
 * has been linted.
 */
function lintDegradation(skillRoot, prefRecord) {
  const notePath = path.join(skillRoot, DISABLED_FILE);
  let note = null;
  try {
    const text = fs.readFileSync(notePath, 'utf8').trim();
    if (text) note = text.split('\n')[0];
  } catch { /* no note: the probe below is the only evidence there is */ }

  const healthy = python3OnPath();
  const reason = note || (healthy ? null : 'python3 is not on PATH, so the hook cannot lint.');

  // A note plus a working interpreter means the hook failed at some point and
  // would work now. Report it once, then clear it, so the install heals instead
  // of carrying a warning about a problem that has gone.
  if (note && healthy) {
    try {
      fs.rmSync(notePath, { force: true });
    } catch { /* it will be reported again next run, which is harmless */ }
  }

  // Keep pref.json in step, so the reason is readable without running status.
  // Best effort: a status run is a read, and must not fail on a read-only home.
  const recorded = prefRecord ? prefRecord[DISABLED_KEY] : undefined;
  if (prefRecord && recorded !== reason) {
    const next = { ...prefRecord };
    if (reason) next[DISABLED_KEY] = reason;
    else delete next[DISABLED_KEY];
    try {
      pref.writePref(next, { drop: reason ? [] : [DISABLED_KEY] });
    } catch { /* reporting the degradation matters more than recording it */ }
  }
  return reason;
}

// ------------------------------------------------------------------- table

// Plain terminal table, no dependencies: measure every cell, pad to the widest.
function renderTable(headers, rows) {
  const widths = headers.map((h, i) =>
    Math.max(h.length, ...rows.map((r) => String(r[i]).length))
  );
  const line = (cells) =>
    cells
      .map((cell, i) => (i === cells.length - 1 ? String(cell) : String(cell).padEnd(widths[i])))
      .join('  ')
      .replace(/\s+$/, '');

  const out = [line(headers), line(widths.map((w) => '-'.repeat(w)))];
  for (const row of rows) out.push(line(row));
  return out;
}

// ------------------------------------------------------------------- layers

/**
 * The two layers of a `both` install, per agent, as table rows.
 *
 * `both` is the only mode where a reader cannot tell what is installed from the
 * mode name alone: the block can be edited out of a CLAUDE.md by hand and the
 * hook entry can be removed by an unrelated settings edit, and each failure is
 * silent on its own. So both are probed on disk and reported side by side —
 * the block by its markers, the hook by its entry in the agent's config.
 *
 * The injector is reported too, and its healthy state is "none". At `both` an
 * injector present would mean the contract reaches the model twice (A31), so
 * seeing it named here is the point.
 */
function layerRows(prefRecord, cwd) {
  const agents = Array.isArray(prefRecord.agents) && prefRecord.agents.length
    ? prefRecord.agents
    : ['claude'];
  const scope = prefRecord.scope || 'global';
  const rows = [];

  for (const agent of agents) {
    const label = adapters.getAdapter(agent).label;
    const target = adapters.memoryTarget(agent, scope, cwd);
    let present = false;
    try {
      present = memory.hasBlock(fs.readFileSync(target, 'utf8'));
    } catch { /* absent file, unreadable file: either way there is no block */ }
    rows.push([label, 'memory block', present ? 'present' : 'ABSENT', target]);

    const wired = agent === 'claude'
      ? hooks.isInstalled({ scope, cwd })
      : hooks.isCodexHookInstalled({ scope, cwd });
    const config = agent === 'claude'
      ? hooks.settingsPath(scope, cwd)
      : hooks.codexHooksPath(scope, cwd);
    rows.push([label, 'Stop hook', wired ? 'wired' : 'ABSENT', config]);

    const injected = agent === 'claude'
      ? hooks.isSessionInstalled({ scope, cwd })
      : hooks.isCodexSessionInstalled({ scope, cwd });
    rows.push([label, 'SessionStart', injected ? 'WIRED' : 'none (by design)', config]);
  }
  return rows;
}

// ------------------------------------------------------------------ command

/**
 * @param {object} [options]
 * @param {Date}   [options.now]   frozen clock, for evals
 * @param {function} [options.write]
 * @returns {number} process exit code — 0 on every expected path (A9, A10)
 */
function run(options = {}) {
  const write = options.write || ((text) => process.stdout.write(text));
  const now = (options.now || new Date()).getTime();
  const cwd = options.cwd || process.cwd();
  const lines = [''];

  const found = pref.findPref();

  if (!found) {
    lines.push('No speakingwords install found.');
    lines.push(MEMORY_LINE);
    lines.push('');
    write(lines.join('\n'));
    return 0;
  }

  const mode = found.pref && found.pref.mode;
  // `both` runs a hook, so it counts hits like hook mode does — and it also has
  // a block, which hook mode does not, so it gets the layer table on top.
  const both = mode === 'both';
  if (mode !== 'hook' && !both) {
    // A10: memory mode (and an unhooked install) exits 0 with one plain line.
    lines.push(`speakingwords is installed in ${mode || 'an unknown'} mode.`);
    lines.push(MEMORY_LINE);
    lines.push('');
    write(lines.join('\n'));
    return 0;
  }

  const skillRoot = path.dirname(found.path);
  const hitsPath = path.join(skillRoot, HITS_FILE);
  const { records, malformed, missing, rotated } = readAllHits(skillRoot);

  // A24: an install that cannot lint says so, rather than looking like an
  // install that simply never caught anything.
  const degraded = lintDegradation(skillRoot, found.pref);
  const warning = degraded ? `  warning  linting is off — ${degraded}` : null;

  // The layer table leads at `both`, because "is each half still installed?" is
  // the question that mode invites and the hit counts answer only half of it.
  if (both) {
    lines.push(
      `speakingwords status — both mode, ${found.pref.voice || 'unknown'} voice, `
      + `${pref.conciseness(found.pref)} conciseness.`
    );
    lines.push('');
    lines.push(...renderTable(
      ['AGENT', 'LAYER', 'STATE', 'TARGET'],
      layerRows(found.pref, cwd)
    ).map((line) => `  ${line}`));
    lines.push('');
    lines.push('  The block prevents, the Stop hook enforces. No SessionStart injector is');
    lines.push('  installed at this mode, so the rules reach the model once, not twice.');
    lines.push('');
  }

  if (records.length === 0) {
    lines.push(
      missing
        ? 'No hits recorded yet — nothing has been caught since install.'
        : 'No hits recorded yet — the log is empty.'
    );
    if (malformed > 0) lines.push(`${malformed} malformed line${malformed === 1 ? '' : 's'} skipped.`);
    if (warning) lines.push(warning);
    lines.push(`  log  ${hitsPath}`);
    lines.push('');
    write(lines.join('\n'));
    return 0;
  }

  const rows = aggregate(records, now);
  const stamps = records.map((r) => parseTs(r.ts)).filter((ms) => ms !== null);
  const first = stamps.length ? Math.min(...stamps) : null;
  const last = stamps.length ? Math.max(...stamps) : null;
  const audits = records.filter((r) => r.audit === true).length;
  const bounces = records.length - audits;

  // The level is read through pref.conciseness(), so a 0.1.0 install with no
  // key reports the `high` the linter is actually applying, not a blank. At
  // `both` the same header was already printed above the layer table.
  if (!both) {
    lines.push(
      `speakingwords status — hook mode, ${found.pref.voice || 'unknown'} voice, `
      + `${pref.conciseness(found.pref)} conciseness.`
    );
    lines.push('');
  }
  lines.push(
    ...renderTable(
      ['RULE', 'HITS', 'SEVERITY', 'EXAMPLE', 'LAST SEEN'],
      rows.map((r) => [r.rule, String(r.count), r.severity, r.example, r.lastSeen])
    )
  );
  lines.push('');

  const span = first === null ? 'unknown' : humanizeSpan(last - first);
  lines.push(
    `  ${records.length} hits across ${rows.length} rule${rows.length === 1 ? '' : 's'}, ` +
    `spanning ${span} (last ${last === null ? 'unknown' : humanizeAgo(now - last)}).`
  );

  // Only worth saying when there is something to separate: an audit-only Codex
  // records what it witnessed, which is not the same as an enforced bounce.
  if (audits > 0) {
    lines.push(
      `  ${bounces} enforced (bounced), ${audits} audit-only (witnessed, nothing blocked).`
    );
  }
  if (malformed > 0) {
    lines.push(`  ${malformed} malformed line${malformed === 1 ? '' : 's'} skipped.`);
  }
  if (warning) lines.push(warning);
  lines.push(`  log  ${hitsPath}`);
  if (rotated) lines.push(`       ${path.join(skillRoot, ROTATED_FILE)} (rotated, also counted)`);
  lines.push('');
  write(lines.join('\n'));
  return 0;
}

module.exports = {
  run,
  readHits,
  readAllHits,
  aggregate,
  layerRows,
  humanizeAgo,
  lintDegradation,
  MEMORY_LINE,
  HITS_FILE,
  ROTATED_FILE,
  DISABLED_FILE,
  DISABLED_KEY,
};
