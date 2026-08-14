'use strict';

// `speakingwords update "<hint>"` — tune the rules from one line of English
// (plan §5).
//
// The hint says what the user wants to see *less* of or *more* of. Less means a
// new strip rule; more means an existing one comes out. Everything lands in
// skill/refs/lexicon.md, because that is the one file the linter reads — so a
// rule added here is enforced on the very next reply, with no reinstall.
//
// Two hard rules:
//   A4  Nothing is edited before a `.bak` of every touched file exists beside
//       the original. If a backup cannot be written, the edit is abandoned and
//       the originals are left exactly as they were. Backups are taken for
//       every target up front, so a failure halfway cannot leave a half-edit.
//   §5  In memory mode the marker block is re-rendered afterwards, so the rules
//       written into CLAUDE.md and the rules in the lexicon never disagree.
//
// Which lexicon gets edited matters. The hook reads the *installed* copy under
// the skill root recorded in pref.json, so that is the target whenever an
// install exists. A dev checkout with no install falls back to the repo copy
// and says so.

const fs = require('node:fs');
const path = require('node:path');

const adapters = require('./adapters');
const { writeFileAtomic } = require('./atomic');
const memory = require('./memory');
const pref = require('./pref');

const REPO_LEXICON = path.join(__dirname, '..', 'skill', 'refs', 'lexicon.md');
const USER_PREFIX = 'strip-user-';
const GUIDANCE = 'User-added via `speakingwords update`. Say it plainly instead.';

const USAGE = `Usage: speakingwords update "<hint>"

The hint is plain English. Say what you want less of, or more of:

  speakingwords update "less emoji"
  speakingwords update "no game-changer, stop saying dive into"
  speakingwords update "more robust"          # allow a word again
  speakingwords update "more concise"         # move the conciseness level up

  less X · no X · stop saying X · ban X · avoid X   ->  adds a strip rule
  more X · allow X · unban X · stop flagging X      ->  removes a strip rule
  more concise · tighter · less verbose             ->  raises the level a step
  less aggressive · more verbose · longer           ->  lowers it a step

The conciseness level is low, med or high, and moves one step at a time.

Every touched file gets a .bak beside it first; if that backup cannot be
written, nothing is edited.`;

// -------------------------------------------------------------- hint parsing

const BAN_RE = /^(?:less|fewer|no|not|never say|never use|don'?t say|don'?t use|stop saying|stop using|stop with|drop|ban|avoid|kill|remove)\s+(.+)$/i;
const ALLOW_RE = /^(?:more|allow|permit|un-?ban|unblock|stop flagging|stop blocking|ok(?:ay)? to say|bring back|restore|keep)\s+(.+)$/i;

// A conciseness hint moves the installed level, not the rule table. These are
// checked before the ban/allow patterns above, because "more concise" and "less
// verbose" would otherwise read as a request to add or remove a strip rule for
// the literal word "concise" — a rule that would then fire on ordinary prose.
//
// The two directions are deliberately not symmetrical in wording: users say
// "tighter" far more often than "less concise", so the list follows how people
// actually phrase it rather than mirroring itself for tidiness.
const TIGHTEN_RE = /\b(?:more concise|more terse|more aggressive|less verbose|less wordy|less padding|less waffle|shorter|tighter|cut more)\b/i;
const LOOSEN_RE = /\b(?:less concise|less terse|less aggressive|more verbose|more detail|more prose|longer|softer|cut less)\b/i;

// "the word emoji", "the phrase 'dive into'", "emojis." -> emoji / dive into
function cleanPhrase(raw) {
  let phrase = String(raw).trim();
  phrase = phrase.replace(/^(?:the\s+)?(?:word|phrase|term|rule)s?\s+/i, '');
  phrase = phrase.replace(/^["'`“”‘’]+|["'`“”‘’]+$/g, '');
  phrase = phrase.replace(/[.!?,;:]+$/g, '');
  phrase = phrase.replace(/\s+/g, ' ').trim();
  return phrase;
}

/**
 * Split a hint into clauses and classify each one.
 *
 * @returns {{intents: Array<{kind:'ban'|'allow', phrase:string, clause:string}>,
 *            levels: Array<{step: 1|-1, clause: string}>, unparsed: string[]}}
 */
function parseHint(hint) {
  const clauses = String(hint || '')
    .split(/[,;]|\band\b|\bbut\b|\bplus\b/i)
    .map((c) => c.trim())
    .filter(Boolean);

  const intents = [];
  const levels = [];
  const unparsed = [];
  for (const clause of clauses) {
    // Level first: these clauses look like ban/allow clauses and are not.
    if (LOOSEN_RE.test(clause)) {
      levels.push({ step: -1, clause });
      continue;
    }
    if (TIGHTEN_RE.test(clause)) {
      levels.push({ step: 1, clause });
      continue;
    }
    const ban = clause.match(BAN_RE);
    const allow = clause.match(ALLOW_RE);
    // "more" and "less" are both prefixes, so whichever matched is the verdict;
    // a clause matching neither is reported rather than guessed at.
    if (allow && (!ban || allow.index < ban.index)) {
      const phrase = cleanPhrase(allow[1]);
      if (phrase) intents.push({ kind: 'allow', phrase, clause });
      else unparsed.push(clause);
    } else if (ban) {
      const phrase = cleanPhrase(ban[1]);
      if (phrase) intents.push({ kind: 'ban', phrase, clause });
      else unparsed.push(clause);
    } else {
      unparsed.push(clause);
    }
  }
  return { intents, levels, unparsed };
}

// ------------------------------------------------------------ lexicon edits

function slugify(phrase) {
  return phrase
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function escapeRegex(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * A word-boundary pattern for a literal phrase.
 *
 * `\b` only means anything next to a word character, so it is applied at each
 * end only where it would actually bind. Internal runs of whitespace become
 * `\s+` so "dive  into" and "dive into" are the same rule.
 */
function patternFor(phrase) {
  const core = escapeRegex(phrase).replace(/\\?\s+/g, '\\s+');
  const lead = /\w/.test(phrase[0]) ? '\\b' : '';
  const tail = /\w/.test(phrase[phrase.length - 1]) ? '\\b' : '';
  return `${lead}${core}${tail}`;
}

// The table cell escaping the lexicon documents: a literal pipe is written \|.
function cellEscape(pattern) {
  return pattern.replace(/\|/g, '\\|');
}

function splitLexicon(text) {
  const lines = text.split('\n');
  let start = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (/^##\s+Strip rules\s*$/.test(lines[i])) { start = i; break; }
  }
  if (start === -1) throw new Error('lexicon has no "## Strip rules" section');
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^##\s+/.test(lines[i])) { end = i; break; }
  }
  return { lines, start, end };
}

function rowCells(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

function isRuleRow(line) {
  if (!line.trim().startsWith('|')) return false;
  const cells = rowCells(line);
  if (cells.length < 4) return false;
  const id = cells[0];
  if (!id || id.toLowerCase() === 'id') return false;
  if (/^[-:\s]+$/.test(id)) return false;
  return true;
}

// The literal words behind a pattern, so "allow robust" finds `\brobust\b`.
function patternSlug(pattern) {
  return slugify(
    pattern
      .replace(/`/g, '')
      .replace(/\\\|/g, '|')
      .replace(/\[[^\]]*\]\*?/g, ' ')
      .replace(/\\[sbwdSBWD]\+?\*?/g, ' ')
      .replace(/[\^$*+?(){}]/g, ' ')
  );
}

function findRules(text, phrase) {
  const { lines, start, end } = splitLexicon(text);
  const slug = slugify(phrase);
  const hits = [];
  for (let i = start; i < end; i += 1) {
    if (!isRuleRow(lines[i])) continue;
    const cells = rowCells(lines[i]);
    const id = cells[0].replace(/^#\s*/, '');
    const idCore = id.replace(/^strip-user-/, '').replace(/^strip-/, '');
    const patSlug = patternSlug(cells[1]);
    if (idCore === slug || patSlug === slug || (slug.length >= 4 && (idCore.includes(slug) || patSlug.includes(slug)))) {
      hits.push({ index: i, id, pattern: cells[1] });
    }
  }
  return hits;
}

/**
 * Apply every intent to the lexicon text.
 *
 * Nothing is written here — this returns the new text plus a change list, so
 * the caller can take backups before anything touches disk.
 */
function applyIntents(text, intents) {
  let current = text;
  const changes = [];
  const notes = [];

  for (const intent of intents) {
    const existing = findRules(current, intent.phrase);

    if (intent.kind === 'ban') {
      if (existing.length > 0) {
        notes.push(`"${intent.phrase}" is already covered by ${existing.map((e) => e.id).join(', ')} — no rule added.`);
        continue;
      }
      const id = `${USER_PREFIX}${slugify(intent.phrase)}`;
      const pattern = patternFor(intent.phrase);
      const row = `| ${id} | \`${cellEscape(pattern)}\` | error | ${GUIDANCE} |`;

      const { lines, start, end } = splitLexicon(current);
      let insertAt = -1;
      for (let i = start; i < end; i += 1) if (isRuleRow(lines[i])) insertAt = i;
      if (insertAt === -1) throw new Error('lexicon strip-rule table has no rows to append after');
      lines.splice(insertAt + 1, 0, row);
      current = lines.join('\n');
      changes.push({ kind: 'add', id, pattern, phrase: intent.phrase });
      continue;
    }

    // allow: take the rule out entirely, rather than parking it, so the table
    // stays the honest list of what is enforced.
    if (existing.length === 0) {
      notes.push(`No rule matched "${intent.phrase}" — nothing to remove.`);
      continue;
    }
    const lines = current.split('\n');
    for (const hit of existing.slice().sort((a, b) => b.index - a.index)) {
      lines.splice(hit.index, 1);
      changes.push({ kind: 'remove', id: hit.id, pattern: hit.pattern, phrase: intent.phrase });
    }
    current = lines.join('\n');
  }

  return { text: current, changes, notes };
}

// ---------------------------------------------------------------- backups

/**
 * Write `<file>.bak` beside every target (A4).
 *
 * Every backup is taken before any edit, so a failure on the second file
 * cannot leave the first one already rewritten.
 *
 * @throws the underlying fs error, with the abort spelled out
 */
function backupAll(files) {
  const written = [];
  for (const file of files) {
    const bak = `${file}.bak`;
    try {
      fs.copyFileSync(file, bak);
      written.push(bak);
    } catch (err) {
      throw new Error(
        `Could not write the backup ${bak} (${err.code || err.message}).\n` +
        'Nothing was edited — speakingwords never edits a file it cannot back up first.'
      );
    }
  }
  return written;
}

// ------------------------------------------------------------------ command

/**
 * Which lexicon to edit.
 *
 * The hook reads the installed copy under the skill root, not the repo, so an
 * install always wins. Memory mode installs no skill core, so the installed
 * copy may not exist yet — in that case it is seeded from the shipped defaults
 * and edited there, which keeps the user's rules under their own install root
 * (plan §7) rather than in a checkout they may not have.
 *
 * Only a machine with no install at all falls back to the repo copy.
 */
function resolveLexicon(found) {
  if (found) {
    const installed = path.join(path.dirname(found.path), 'refs', 'lexicon.md');
    if (fs.existsSync(installed)) return { file: installed, fallback: false, seeded: false };
    try {
      fs.mkdirSync(path.dirname(installed), { recursive: true });
      fs.copyFileSync(REPO_LEXICON, installed);
      return { file: installed, fallback: false, seeded: true };
    } catch {
      // Unwritable install root: better to edit the repo copy and say so than
      // to refuse the edit outright.
    }
  }
  return { file: REPO_LEXICON, fallback: true, seeded: false };
}

/**
 * @param {string} hint
 * @param {object} [options]
 * @returns {number} exit code: 0 edited or nothing to do, 1 refused
 */
function run(hint, options = {}) {
  const write = options.write || ((text) => process.stdout.write(text));
  const writeErr = options.writeErr || ((text) => process.stderr.write(text));
  const cwd = options.cwd || process.cwd();

  if (!hint || !String(hint).trim()) {
    writeErr(`${USAGE}\n`);
    return 1;
  }

  const { intents, levels, unparsed } = parseHint(hint);
  if (intents.length === 0 && levels.length === 0) {
    // Nothing recognised means nothing is edited. Guessing at a hint would put
    // a rule the user never asked for in front of every future reply.
    writeErr(
      `\nCould not read "${String(hint).trim()}" as a rule change — nothing was edited.\n\n${USAGE}\n`
    );
    return 1;
  }

  const found = pref.findPref();
  const { file: lexiconPath, fallback, seeded } = resolveLexicon(found);

  // --- Conciseness moves, resolved before anything is written ---
  // The level lives in pref.json, not the lexicon, so this is a separate edit
  // with its own backup. A hint with no install to write to is refused rather
  // than silently dropped: there is nowhere for the level to live.
  const levelNote = [];
  let nextLevel = null;
  let prevLevel = null;
  if (levels.length) {
    if (!found) {
      writeErr(
        '\nThere is no speakingwords install to change the conciseness level on.\n'
        + 'Run `speakingwords init` first — nothing was edited.\n'
      );
      return 1;
    }
    const step = levels.reduce((sum, l) => sum + l.step, 0);
    const current = pref.conciseness(found.pref);
    if (step === 0) {
      levelNote.push(`Conciseness hints cancelled out — level stays ${current}.`);
    } else {
      const at = pref.LEVELS.indexOf(current);
      const target = pref.LEVELS[Math.min(pref.LEVELS.length - 1, Math.max(0, at + Math.sign(step)))];
      if (target === current) {
        levelNote.push(
          `Conciseness is already ${current}, which is as ${step > 0 ? 'tight' : 'loose'} as it goes.`
        );
      } else {
        nextLevel = target;
        prevLevel = current;
      }
    }
  }

  let before;
  try {
    before = fs.readFileSync(lexiconPath, 'utf8');
  } catch (err) {
    writeErr(`Cannot read the lexicon at ${lexiconPath} (${err.code || err.message}).\n`);
    return 1;
  }

  let result;
  try {
    result = applyIntents(before, intents);
  } catch (err) {
    writeErr(`${err.message}\nNothing was edited.\n`);
    return 1;
  }

  const lines = [''];
  const memoryMode = Boolean(found && found.pref && found.pref.mode === 'memory');

  if (result.changes.length === 0 && !nextLevel) {
    lines.push('No change to make.');
    for (const note of levelNote) lines.push(`  ${note}`);
    for (const note of result.notes) lines.push(`  ${note}`);
    for (const clause of unparsed) lines.push(`  Did not understand "${clause}".`);
    lines.push('');
    write(lines.join('\n'));
    return 0;
  }

  // Memory mode re-renders the block, so those files are edited too and need
  // their own backups before anything is written (plan §5, A4).
  const memoryTargets = [];
  if (memoryMode) {
    const agents = Array.isArray(found.pref.agents) && found.pref.agents.length
      ? found.pref.agents
      : ['claude'];
    for (const agent of agents) {
      const target = adapters.memoryTarget(agent, found.pref.scope || 'global', cwd);
      if (fs.existsSync(target)) memoryTargets.push(target);
    }
  }

  // A level move rewrites pref.json, so it is a touched file and gets a backup
  // like every other one (A4).
  const prefTargets = nextLevel && found && fs.existsSync(found.path) ? [found.path] : [];
  const lexiconTargets = result.changes.length ? [lexiconPath] : [];

  let backups;
  try {
    backups = backupAll([...lexiconTargets, ...prefTargets, ...memoryTargets]);
  } catch (err) {
    writeErr(`\n${err.message}\n`);
    return 1;
  }

  // Atomic (A22): the hook reads this file on every reply, so a torn write
  // would be read by the linter, not just left on disk.
  if (result.changes.length) writeFileAtomic(lexiconPath, result.text);

  if (nextLevel) {
    // writePref preserves every key it was not handed, so this moves the level
    // and nothing else — including keys written by a version that is not this one.
    pref.writePref({ ...found.pref, conciseness: nextLevel });
    found.pref.conciseness = nextLevel;
  }

  const rerendered = [];
  if (memoryMode) {
    const block = memory.renderBlock({
      voice: found.pref.voice || 'terse',
      conciseness: pref.conciseness(found.pref),
      version: found.pref.version || undefined,
      lexiconPath,
    });
    const agents = Array.isArray(found.pref.agents) && found.pref.agents.length
      ? found.pref.agents
      : ['claude'];
    for (const agent of agents) {
      const target = adapters.memoryTarget(agent, found.pref.scope || 'global', cwd);
      rerendered.push(memory.writeBlock(target, block));
    }
  }

  // --- Summary: the diff, in the terms the user asked in ---
  if (result.changes.length) {
    lines.push(`Updated ${lexiconPath}`);
    lines.push('');
    for (const change of result.changes) {
      lines.push(change.kind === 'add'
        ? `  + ${change.id}   ${change.pattern}   (bans "${change.phrase}")`
        : `  - ${change.id}   ${change.pattern}   (allows "${change.phrase}" again)`);
    }
    lines.push('');
  }
  if (nextLevel) {
    lines.push(`Conciseness is now ${nextLevel} (was ${prevLevel}).`);
    lines.push(`  recorded in ${found.path}`);
    lines.push('');
  }
  for (const bak of backups) lines.push(`  backup  ${bak}`);
  if (rerendered.length) {
    lines.push('');
    for (const r of rerendered) lines.push(`  memory block re-rendered in ${r.path}`);
    lines.push('  Memory and lexicon now say the same thing.');
  }
  for (const note of levelNote) lines.push(`  ${note}`);
  for (const note of result.notes) lines.push(`  ${note}`);
  for (const clause of unparsed) lines.push(`  Did not understand "${clause}" — skipped.`);
  if (fallback) {
    lines.push('');
    lines.push('  Note: no install was found, so the repo copy was edited.');
    lines.push('  Run `speakingwords init` to install these rules where the agent reads them.');
  } else if (seeded) {
    lines.push('');
    lines.push('  Note: this install had no lexicon of its own yet, so one was seeded from the');
    lines.push('  shipped defaults and your change applied to it.');
  } else if (found && found.pref && found.pref.mode === 'hook') {
    lines.push('');
    lines.push('  The hook reads this file live — the change is in force on the next reply.');
  }
  lines.push('');
  write(lines.join('\n'));
  return 0;
}

module.exports = {
  run,
  USAGE,
  parseHint,
  applyIntents,
  patternFor,
  findRules,
  slugify,
};
