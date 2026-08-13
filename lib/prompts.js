'use strict';

// Minimal readline prompt helpers. No dependencies: node:readline only.
//
// Input is consumed through a line queue rather than rl.question(). When stdin
// is a pipe (scripts, CI, `printf ... | speakingwords init`) the whole input
// arrives in one chunk, and rl.question() only ever catches the line that lands
// while it happens to be waiting — later answers are dropped and the next
// question sees EOF. Queuing every 'line' event fixes that, and behaves the
// same on a real terminal.
//
//   ask(text, {default})            free text with a default
//   choose(text, choices, default)  numbered list, default marked, returns the
//                                   chosen choice's `value`
//   confirm(text, defaultYes)       y/n
//   close()                         release stdin; the caller must call it

const readline = require('node:readline');

let rl = null;
const queue = [];
let waiter = null;
let ended = false;

function iface() {
  if (rl) return rl;
  rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  rl.on('line', (line) => {
    if (waiter) {
      const resolve = waiter.resolve;
      waiter = null;
      resolve(line);
    } else {
      queue.push(line);
    }
  });
  rl.on('close', () => {
    ended = true;
    if (waiter) {
      const reject = waiter.reject;
      waiter = null;
      reject(new Error('Input ended before the question was answered.'));
    }
  });
  return rl;
}

function close() {
  if (rl) rl.close();
  rl = null;
  queue.length = 0;
  waiter = null;
  ended = false;
}

function question(text) {
  iface();
  process.stdout.write(text);
  if (queue.length) return Promise.resolve(queue.shift());
  if (ended) return Promise.reject(new Error('Input ended before the question was answered.'));
  return new Promise((resolve, reject) => { waiter = { resolve, reject }; });
}

async function ask(text, options = {}) {
  const suffix = options.default ? ` [${options.default}]` : '';
  const answer = (await question(`${text}${suffix}: `)).trim();
  return answer || options.default || '';
}

// choices: [{ value, label, hint? }]
// defaultValue: the `value` selected by pressing Enter.
async function choose(text, choices, defaultValue) {
  if (!Array.isArray(choices) || choices.length === 0) {
    throw new Error('choose() needs at least one choice');
  }
  const defIndex = Math.max(0, choices.findIndex((c) => c.value === defaultValue));

  for (;;) {
    const lines = [`\n${text}`];
    choices.forEach((choice, i) => {
      const marker = i === defIndex ? ' (default)' : '';
      const hint = choice.hint ? ` — ${choice.hint}` : '';
      lines.push(`  ${i + 1}) ${choice.label}${hint}${marker}`);
    });
    process.stdout.write(`${lines.join('\n')}\n`);

    const raw = (await question(`Choose 1-${choices.length} [${defIndex + 1}]: `)).trim();
    if (raw === '') return choices[defIndex].value;

    const n = Number.parseInt(raw, 10);
    if (Number.isInteger(n) && n >= 1 && n <= choices.length) return choices[n - 1].value;

    // Also accept the value name itself, e.g. "terse".
    const byValue = choices.find((c) => c.value.toLowerCase() === raw.toLowerCase());
    if (byValue) return byValue.value;

    process.stdout.write(`Not a valid choice: ${raw}\n`);
  }
}

async function confirm(text, defaultYes = false) {
  const suffix = defaultYes ? ' [Y/n]' : ' [y/N]';
  const answer = (await question(`${text}${suffix}: `)).trim().toLowerCase();
  if (answer === '') return defaultYes;
  return answer === 'y' || answer === 'yes';
}

module.exports = { ask, choose, confirm, close };
