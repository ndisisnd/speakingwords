'use strict';

// `speakingwords help` — the only documentation the tool ships at the terminal
// (plan v0.2.0 §2 W1, assertions A14–A18).
//
// The whole point of this module is that there is ONE table. `COMMANDS` below
// is the single source of truth: the overview renders from it, the list of
// valid `help <topic>` topics derives from it, and each topic page is a slice
// of the same rows. A command cannot appear in the overview and be missing a
// topic page, or vice versa, because neither list is written down twice (A15).
//
// The contract this module has to keep:
//
//   A14  `help`, no command at all, and `-h`/`--help` print byte-identical
//        overview text — they all call overview(), so identity is structural,
//        not something a test has to police after the fact.
//   A16  Help the user asked for exits 0. Help shown *at* the user, after an
//        unknown command or a bad flag value, exits 1.
//   A17  An unknown topic exits non-zero, prints the full overview so the user
//        can see what the real topics are, and names the topic they typed.
//        Never a stack trace.
//   A18  Success goes to stdout, errors go to stderr, so `speakingwords help >
//        file` captures clean help and nothing else.
//
// Formatting is deliberately hand-rolled and dependency-free, same as the
// `status` table: measure the widest label, pad everything to it.

// Two spaces of indent, then the description column starts two spaces past the
// longest usage label. Flag lines share the same column so the whole page reads
// as one grid.
const INDENT = '  ';

const HEADER = 'speakingwords — keep agent replies in the shape you asked for.';
// v0.2.0 makes it four: conciseness joins voice as an independent axis, and
// merging the two into one picker would hide that any voice pairs with any
// level (plan §8). The 0.1.0 wording is retired here deliberately.
const FOOTER = 'Without flags, init asks four questions: mode, agent + scope, voice, conciseness.';

/**
 * Every command the dispatcher accepts, in the order the overview lists them.
 *
 *   name     the command word
 *   aliases  other words the dispatcher accepts for the same command
 *   usage    the usage-line label; omitted for commands the overview does not
 *            list as a row of its own (`help` itself)
 *   summary  the one-line description, used by the overview row and as the
 *            title of the topic page
 *   section  the command's own block in the overview (flags, examples, notes),
 *            reused verbatim by its topic page
 *   gotcha   the one thing that surprises people — exactly one per command
 */
const COMMANDS = [
  {
    name: 'init',
    usage: 'speakingwords init [flags]',
    summary: 'install a style contract',
    section: {
      heading: 'init flags (skip the questions, for scripts and CI)',
      flags: [
        ['--memory', 'memory mode: write rules into the memory file'],
        ['--hook', 'hook mode: lint and bounce every reply'],
        ['--both', 'both modes: the block prevents, the hook enforces'],
        ['--agent claude|codex|both', 'which agent to install for'],
        ['--scope local|global', 'this project only, or everywhere'],
        ['--voice terse|convo', 'point form only, or prose retained'],
        ['--conciseness low|high', 'how much of a reply survives'],
        ['-h, --help', 'this text'],
      ],
    },
    gotcha:
      'Re-running init replaces the installed block or hook entry in place — it never\n' +
      'stacks a second copy, and nothing outside the speakingwords markers is touched.\n' +
      'At --both the rules are stated once, not twice: the block carries them into every\n' +
      'session, so no SessionStart hook is wired.',
  },
  {
    name: 'version',
    usage: 'speakingwords version',
    summary: 'print the installed version',
    gotcha:
      'This prints the version of the CLI you just ran. The version recorded at install\n' +
      'time lives in pref.json, and the two drift apart after an upgrade until you\n' +
      're-run init.',
  },
  {
    name: 'status',
    usage: 'speakingwords status',
    summary: 'show what the linter caught (hook mode)',
    gotcha:
      'Only a running linter counts anything, so hook mode and both mode have a table\n' +
      'and memory mode does not — there, status exits 0 with one explanatory line.\n' +
      'At both mode it also reports whether each layer is still installed.',
  },
  {
    name: 'update',
    usage: 'speakingwords update "<hint>"',
    summary: 'tune the rules from one line of English',
    section: {
      heading: 'update hints say what you want less of, or more of:',
      examples: [
        'speakingwords update "less emoji"',
        'speakingwords update "no game-changer, stop saying dive into"',
        'speakingwords update "more robust"     allow a word again',
        'speakingwords update "more concise"    move the conciseness level up a step',
      ],
      notes: ['Every file it edits gets a .bak beside it first.'],
    },
    gotcha:
      'Hints are matched, not guessed. A hint it does not recognise changes nothing and\n' +
      'exits 1 — phrase it as "less X" or "more X" and it will land.',
  },
  {
    name: 'unhook',
    aliases: ['unset'],
    usage: 'speakingwords unhook [--yes]',
    summary: 'remove hook wiring (alias: unset)',
    section: {
      heading: 'unhook flags',
      flags: [['-y, --yes', 'skip the confirmation prompt']],
    },
    gotcha:
      'It removes the wiring, not the history. hits.jsonl and the installed skill files\n' +
      'stay put, so a later init picks up where you left off. On a both-mode install it\n' +
      'is a downgrade: the hook goes, the memory block stays, and the mode becomes\n' +
      'memory.',
  },
  {
    name: 'help',
    summary: 'print this overview, or help for one command',
    section: {
      heading: 'help topics',
      examples: [
        'speakingwords help              this overview',
        'speakingwords help update       one command: what it does, flags, gotcha',
      ],
    },
    gotcha:
      'Topic help and this overview render from one table, so a command can never show\n' +
      'up in one and be missing from the other.',
  },
];

// --------------------------------------------------------------- lookups

/** Every word `help <topic>` accepts, commands and aliases alike (A15). */
function topics() {
  const out = [];
  for (const command of COMMANDS) {
    out.push(command.name);
    for (const alias of command.aliases || []) out.push(alias);
  }
  return out;
}

/** Resolve a topic word to its command, or null. Aliases resolve to the real one. */
function findCommand(topic) {
  if (typeof topic !== 'string' || !topic) return null;
  const wanted = topic.trim().toLowerCase();
  for (const command of COMMANDS) {
    if (command.name === wanted) return command;
    if ((command.aliases || []).includes(wanted)) return command;
  }
  return null;
}

// -------------------------------------------------------------- rendering

// One column for the whole page: two past the longest usage label. Flag lines
// use it too, so usage rows and flag rows line up down the page.
function descriptionColumn() {
  const widest = COMMANDS.filter((c) => c.usage).reduce(
    (max, c) => Math.max(max, c.usage.length),
    0
  );
  return widest + 2;
}

function padded(label, description) {
  const column = descriptionColumn();
  return `${INDENT}${label.padEnd(column)}${description}`.replace(/\s+$/, '');
}

/** A command's own block: heading, then flags / examples / notes as present. */
function sectionLines(command) {
  const section = command.section;
  if (!section) return [];
  const lines = [section.heading];
  for (const [flag, description] of section.flags || []) lines.push(padded(flag, description));
  for (const example of section.examples || []) lines.push(`${INDENT}${example}`);
  for (const note of section.notes || []) lines.push(note);
  return lines;
}

/**
 * The full overview. Every trigger — `help`, no args, `-h`, `--help` — renders
 * through this one function, which is what makes A14 structural.
 *
 * @returns {string} without a trailing newline
 */
function overview() {
  const lines = [HEADER, '', 'Usage'];
  for (const command of COMMANDS) {
    if (command.usage) lines.push(padded(command.usage, command.summary));
  }
  for (const command of COMMANDS) {
    // `help`'s own section is a topic-page detail; the overview IS that page,
    // so repeating it here would be the tool explaining itself twice.
    if (!command.section || command.name === 'help') continue;
    lines.push('', ...sectionLines(command));
  }
  lines.push('', FOOTER);
  return lines.join('\n');
}

/**
 * One command's page: the same slice of the overview it already owns, plus its
 * gotcha. Nothing here is authored separately, so nothing here can drift.
 *
 * @param {string} topic command name or alias
 * @returns {string|null} page text, or null when the topic is unknown
 */
function topicPage(topic) {
  const command = findCommand(topic);
  if (!command) return null;

  const asked = String(topic).trim().toLowerCase();
  const lines = ['', `speakingwords ${command.name} — ${command.summary}`, ''];

  if (asked !== command.name) {
    lines.push(`${INDENT}\`${asked}\` is an alias for \`${command.name}\`; they run the same code.`, '');
  }
  if (command.usage) lines.push(`${INDENT}${command.usage}`, '');

  const section = sectionLines(command);
  if (section.length) lines.push(...section, '');

  lines.push('Gotcha');
  for (const line of command.gotcha.split('\n')) lines.push(`${INDENT}${line}`);
  lines.push('', `${INDENT}speakingwords help              every command`);
  lines.push('');
  return lines.join('\n');
}

// ---------------------------------------------------------------- command

/**
 * `speakingwords help [topic]`, and every other path that shows help.
 *
 * @param {object}   [options]
 * @param {string}   [options.topic]    the word after `help`, if any
 * @param {string}   [options.reason]   error line to print above the overview
 *                                      (unknown command, bad flag value)
 * @param {function} [options.write]    stdout sink
 * @param {function} [options.writeErr] stderr sink
 * @returns {number} exit code — 0 when the user asked for help, 1 otherwise
 */
function run(options = {}) {
  const write = options.write || ((text) => process.stdout.write(text));
  const writeErr = options.writeErr || ((text) => process.stderr.write(text));

  // Help shown *at* the user rather than asked for by them: unknown command or
  // a bad flag value. Overview goes to stderr with the reason, exit 1 (A16, A18).
  if (options.reason) {
    writeErr(`${options.reason}\n\n${overview()}\n`);
    return 1;
  }

  if (options.topic) {
    const page = topicPage(options.topic);
    if (page) {
      write(`${page}\n`);
      return 0;
    }
    // A17: name the bad topic, show the real ones, never a stack trace.
    writeErr(
      `Unknown help topic: ${options.topic}\n` +
      `Topics: ${topics().join(', ')}\n\n${overview()}\n`
    );
    return 1;
  }

  write(`${overview()}\n`);
  return 0;
}

module.exports = { run, overview, topicPage, topics, findCommand, COMMANDS };
