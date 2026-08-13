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

const pref = require('./pref');

const HITS_FILE = 'hits.jsonl';
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
  if (mode !== 'hook') {
    // A10: memory mode (and an unhooked install) exits 0 with one plain line.
    lines.push(`speakingwords is installed in ${mode || 'an unknown'} mode.`);
    lines.push(MEMORY_LINE);
    lines.push('');
    write(lines.join('\n'));
    return 0;
  }

  const skillRoot = path.dirname(found.path);
  const hitsPath = path.join(skillRoot, HITS_FILE);
  const { records, malformed, missing } = readHits(hitsPath);

  if (records.length === 0) {
    lines.push(
      missing
        ? 'No hits recorded yet — nothing has been caught since install.'
        : 'No hits recorded yet — the log is empty.'
    );
    if (malformed > 0) lines.push(`${malformed} malformed line${malformed === 1 ? '' : 's'} skipped.`);
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

  lines.push(`speakingwords status — hook mode, ${found.pref.voice || 'unknown'} voice.`);
  lines.push('');
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
  lines.push(`  log  ${hitsPath}`);
  lines.push('');
  write(lines.join('\n'));
  return 0;
}

module.exports = { run, readHits, aggregate, humanizeAgo, MEMORY_LINE, HITS_FILE };
