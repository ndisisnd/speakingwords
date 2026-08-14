'use strict';

// Atomic file writes (plan §2 W4.2, assertion A22).
//
// Every file speakingwords owns — pref.json, the lexicon, the memory block, the
// agents' own settings files — is written temp-file-then-rename. A crash, a
// kill, or a full disk mid-write then leaves either the old file or the new
// one, never a half-written one. That matters because these files are read at
// arbitrary moments by things that are not us: the hook reads the lexicon on
// every reply, the agent reads its settings on every start.
//
// The temp file sits in the destination directory on purpose. rename() is only
// atomic within one filesystem, so a temp in /tmp would silently degrade to a
// copy across a device boundary and lose the guarantee.

const fs = require('node:fs');
const path = require('node:path');

let counter = 0;

function tempPath(file) {
  counter += 1;
  return path.join(
    path.dirname(file),
    `.${path.basename(file)}.${process.pid}.${counter}.tmp`
  );
}

/**
 * Write `data` to `file` in one indivisible step.
 *
 * The mode of an existing file is carried over: a rename replaces the inode, so
 * without it a 0600 config would come back as 0644 after an edit.
 *
 * @returns {string} the file written
 */
function writeFileAtomic(file, data, encoding = 'utf8') {
  fs.mkdirSync(path.dirname(file), { recursive: true });

  let mode = 0o666;
  try {
    mode = fs.statSync(file).mode;
  } catch { /* new file: default mode */ }

  const tmp = tempPath(file);
  try {
    const fd = fs.openSync(tmp, 'w', mode);
    try {
      fs.writeFileSync(fd, data, encoding);
      // Flush before the rename: a rename that lands ahead of the data is how
      // "atomic" writes still produce empty files after a power cut.
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, file);
  } catch (err) {
    try {
      fs.rmSync(tmp, { force: true });
    } catch { /* best effort: never mask the real error */ }
    throw err;
  }
  return file;
}

module.exports = { writeFileAtomic };
