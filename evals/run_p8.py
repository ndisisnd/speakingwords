#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 8 (stability hardening).

No model calls, no network. SPEAKINGWORDS_HOME fakes the home directory, so
every install path here runs in a throwaway temp tree.

What is gated here
------------------
  A21  The lexicon cache never changes a verdict. E10 runs the full E1 fixture
       set cold (no cache), warm (cache present) and corrupted (garbage cache)
       and diffs lint.py's stdout byte for byte across all three.
  A22  Every config write is temp-file-then-rename. Killing a writer mid-flight,
       over and over, leaves a pref.json that still parses and a lexicon that
       still compiles — always one whole version or the other, never a blend.
  A23  hits.jsonl rotates at 1 MB into a single hits.jsonl.1, and `status`
       totals are identical immediately before and after the boundary.
  A24  With python3 masked from PATH the hook exits 0, blocks nothing, and
       `status` explains the degradation instead of reporting silence.
  A25  A 0.1.0 pref.json (no conciseness key) works with every util, and a
       rewrite preserves keys this version has never heard of.

  E10  Cache parity, plus the timing claim the cache exists for: a warm parse
       beats a cold one over 100 runs, and warm lint stays inside the E6 budget.

Usage:  python3 evals/run_p8.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "bin", "speakingwords.js")
SCRIPTS = os.path.join(ROOT, "skill", "scripts")
LINT = os.path.join(SCRIPTS, "lint.py")
LEXICON = os.path.join(ROOT, "skill", "refs", "lexicon.md")
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "manifest.json")

E6_P95_MAX_MS = 100.0
TIMING_RUNS = 100
ROTATION_BYTES = 1024 * 1024

MODERN_CODEX = "0.124.0"

# Import lint.py the way the hook does: by path, and without leaving a
# __pycache__ behind in the shipped tree.
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import lint as lint_mod  # noqa: E402

results = []
# Numbers worth seeing even when the gate passes — a cache that only just wins,
# or a p95 creeping towards the budget, is a warning before it is a failure.
notes = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def run_cli(args, home, cwd, stdin="", codex_version=MODERN_CODEX):
    env = dict(os.environ, SPEAKINGWORDS_HOME=home, SPEAKINGWORDS_CODEX_VERSION=codex_version)
    return subprocess.run(
        ["node", CLI] + args, cwd=cwd, env=env, input=stdin, capture_output=True, text=True
    )


def make_home():
    home = tempfile.mkdtemp(prefix="speakingwords-home-")
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    os.makedirs(os.path.join(home, ".codex"), exist_ok=True)
    return home


def make_project():
    return tempfile.mkdtemp(prefix="speakingwords-proj-")


def claude_root(home):
    return os.path.join(home, ".claude", "skills", "speakingwords")


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_manifest():
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fixture_paths(manifest):
    paths = [os.path.join(FIXTURES, "violations", n) for n in sorted(manifest["violations"])]
    paths += [os.path.join(FIXTURES, "clean", n) for n in sorted(manifest["clean"])]
    return paths


def drop_cache(path=LEXICON):
    try:
        os.unlink(lint_mod.cache_path(path))
    except OSError:
        pass


def corrupt_cache(path=LEXICON):
    with open(lint_mod.cache_path(path), "wb") as fh:
        fh.write(b"\x00not json at all{{{")


# ------------------------------------------------------- A21 / E10: cache


def lint_stdout(path):
    """Raw bytes lint.py printed, plus its exit code — the whole verdict."""
    proc = subprocess.run(
        [sys.executable, LINT, "--voice", "convo", path], capture_output=True
    )
    return proc.returncode, proc.stdout


def eval_cache_parity(manifest):
    paths = fixture_paths(manifest)

    cold = {}
    for path in paths:
        drop_cache()  # every cold run starts from nothing
        cold[path] = lint_stdout(path)

    lint_mod.read_rules()  # leave a good cache behind
    warm = {path: lint_stdout(path) for path in paths}

    corrupted = {}
    for path in paths:
        corrupt_cache()  # a clean run rewrites it, so re-corrupt each time
        corrupted[path] = lint_stdout(path)

    warm_diff = [os.path.basename(p) for p in paths if warm[p] != cold[p]]
    corrupt_diff = [os.path.basename(p) for p in paths if corrupted[p] != cold[p]]

    check("A21", "warm-cache output is byte-identical to cold across %d fixtures" % len(paths),
          not warm_diff, ", ".join(warm_diff[:6]))
    check("A21", "corrupt-cache output is byte-identical to cold",
          not corrupt_diff, ", ".join(corrupt_diff[:6]))
    check("E10", "all three cache states agree on every fixture",
          not warm_diff and not corrupt_diff)

    # A deleted cache is the cold path, already covered above; a *stale* one is
    # the case the mtime+size key exists for. Done on a copy, so the repo
    # lexicon is never edited by an eval.
    tmp = tempfile.mkdtemp(prefix="speakingwords-lex-")
    try:
        copy = os.path.join(tmp, "lexicon.md")
        shutil.copyfile(LEXICON, copy)
        before = len(lint_mod.read_rules(copy))
        text = read(copy)
        row = "| strip-user-zorkmid | `\\bzorkmid\\b` | error | fault-test row |"
        lines = text.split("\n")
        last = max(i for i, l in enumerate(lines) if l.strip().startswith("| strip-"))
        lines.insert(last + 1, row)
        # Same second, different bytes: the size half of the key has to carry it.
        stat = os.stat(copy)
        write(copy, "\n".join(lines))
        os.utime(copy, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        after = [r[0] for r in lint_mod.read_rules(copy)]
        check("A21", "an edited lexicon invalidates the cache", "strip-user-zorkmid" in after,
              "%d rules before, %d after" % (before, len(after)))

        # And the cache is a file, not a promise: deleting it changes nothing.
        drop_cache(copy)
        check("A21", "deleting the cache leaves the same rules",
              [r[0] for r in lint_mod.read_rules(copy)] == after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_timing(mode, runs=TIMING_RUNS):
    """Time the first read_rules() call in a fresh interpreter, `runs` times.

    Measured in-process and printed by the child, because end-to-end timing is
    dominated by interpreter startup and would drown the thing under test.
    """
    script = (
        "import os,sys,time\n"
        "sys.dont_write_bytecode=True\n"
        "sys.path.insert(0,%r)\n"
        "import lint\n"
        "if %r=='cold':\n"
        "    try: os.unlink(lint.cache_path(lint.LEXICON_PATH))\n"
        "    except OSError: pass\n"
        "t=time.perf_counter()\n"
        "lint.read_rules()\n"
        "print((time.perf_counter()-t)*1000)\n" % (SCRIPTS, mode)
    )
    samples = []
    for _ in range(runs):
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        samples.append(float(proc.stdout.strip()))
    return samples


def eval_cache_timing(manifest):
    cold = parse_timing("cold")
    lint_mod.read_rules()
    warm = parse_timing("warm")

    cold_median = statistics.median(cold)
    warm_median = statistics.median(warm)
    detail = "cold median %.3f ms, warm median %.3f ms" % (cold_median, warm_median)
    notes.append("read_rules first call, %d runs each: %s" % (TIMING_RUNS, detail))
    check("E10", "warm parse beats cold over %d runs" % TIMING_RUNS,
          warm_median < cold_median, detail)

    # And the whole invocation still fits the E6 budget with the cache in play.
    perf = os.path.join(FIXTURES, manifest["perf"])
    samples = []
    for _ in range(TIMING_RUNS):
        t0 = time.perf_counter()
        lint_stdout(perf)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p95 = samples[int(round(0.95 * (len(samples) - 1)))]
    notes.append("warm lint end-to-end p95: %.2f ms (E6 gate <%.0f ms)" % (p95, E6_P95_MAX_MS))
    check("E10", "warm lint p95 stays under the E6 budget", p95 < E6_P95_MAX_MS,
          "p95 %.2f ms (gate <%.0f ms)" % (p95, E6_P95_MAX_MS))


# ------------------------------------------------------ A22: atomic writes


PREF_DRIVER = """
const pref = require(process.argv[2] + '/lib/pref.js');
const big = 'x'.repeat(200000);
for (let i = 0; ; i += 1) {
  pref.writePref({
    agents: ['claude'], mode: 'hook', scope: 'global', voice: 'terse',
    version: '0.2.0', pad: i % 2 === 0 ? 'x' : big,
  });
}
"""

LEXICON_DRIVER = """
const fs = require('node:fs');
const { writeFileAtomic } = require(process.argv[2] + '/lib/atomic.js');
const target = process.argv[3];
const a = fs.readFileSync(process.argv[4], 'utf8');
const b = a + '\\n<!-- padding -->'.repeat(4000) + '\\n';
for (let i = 0; ; i += 1) writeFileAtomic(target, i % 2 === 0 ? a : b);
"""


def kill_mid_write(driver, args, env, rounds=12):
    """Start a writer, kill -9 it mid-flight, return what was on disk after."""
    tmp = tempfile.mkdtemp(prefix="speakingwords-kill-")
    script = os.path.join(tmp, "driver.js")
    write(script, driver)
    seen = []
    try:
        for i in range(rounds):
            proc = subprocess.Popen(
                ["node", script] + args, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            # Spread the kill across the write cycle so it lands in different
            # places: open, write, fsync, rename.
            time.sleep(0.05 + (i % 6) * 0.03)
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            seen.append(i)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return len(seen)


def eval_atomic_pref():
    home = make_home()
    try:
        env = dict(os.environ, SPEAKINGWORDS_HOME=home)
        target = os.path.join(claude_root(home), "pref.json")
        os.makedirs(claude_root(home), exist_ok=True)

        rounds = kill_mid_write(PREF_DRIVER, [ROOT], env)

        text = read(target)
        parsed = json.loads(text)  # a torn write throws here
        check("A22", "pref.json parses after %d kill-mid-write rounds" % rounds, True)
        check("A22", "pref.json is one whole version, not a blend",
              len(parsed.get("pad", "")) in (1, 200000), len(parsed.get("pad", "")))
        check("A22", "the interrupted writes left no temp file in place of the config",
              os.path.isfile(target))
    except ValueError as exc:
        check("A22", "pref.json parses after kill-mid-write rounds", False, str(exc))
    finally:
        shutil.rmtree(home, ignore_errors=True)


def eval_atomic_lexicon():
    tmp = tempfile.mkdtemp(prefix="speakingwords-lex-")
    try:
        target = os.path.join(tmp, "lexicon.md")
        shutil.copyfile(LEXICON, target)
        rounds = kill_mid_write(
            LEXICON_DRIVER, [ROOT, target, LEXICON], dict(os.environ)
        )

        text = read(target)
        a = read(LEXICON)
        b = a + "\n<!-- padding -->" * 4000 + "\n"
        check("A22", "the lexicon is one whole version after %d kill rounds" % rounds,
              text in (a, b), "%d bytes on disk" % len(text))
        try:
            rules = lint_mod.parse_rules(target)
            ok, detail = bool(rules), "%d rules" % len(rules)
        except Exception as exc:  # a torn lexicon would land here
            ok, detail = False, repr(exc)
        check("A22", "the lexicon still compiles after kill-mid-write", ok, detail)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def eval_atomic_coverage():
    """Every whole-file config write goes through the atomic helper."""
    offenders = []
    for name in ("pref.js", "memory.js", "update.js", "hooks.js", "status.js"):
        text = read(os.path.join(ROOT, "lib", name))
        for i, line in enumerate(text.split("\n"), 1):
            if "writeFileSync" in line and "//" not in line.split("writeFileSync")[0]:
                offenders.append("lib/%s:%d" % (name, i))
    check("A22", "no lib/ config write bypasses the atomic helper", not offenders,
          ", ".join(offenders))


# -------------------------------------------------- A23: telemetry rotation


def hits_line(rule, i):
    return json.dumps({
        "ts": "2026-08-14T09:%02d:%02dZ" % (i // 60 % 60, i % 60),
        "rule": rule,
        "match": "Landed" if rule == "strip-landed" else "delving",
        "severity": "error",
        "voice": "terse",
        # Padding so a realistic record count crosses 1 MB.
        "note": "x" * 120,
    })


def totals_block(out):
    """The part of `status` that reports counts: table plus summary line."""
    lines = out.split("\n")
    return "\n".join(l for l in lines if not l.strip().startswith("log")
                     and "rotated, also counted" not in l)


def eval_rotation():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"],
                home, project)
        root = claude_root(home)
        hits = os.path.join(root, "hits.jsonl")
        rotated = hits + ".1"

        records = [hits_line("strip-landed" if i % 3 else "strip-delve", i) for i in range(6000)]
        write(hits, "\n".join(records) + "\n")
        size = os.path.getsize(hits)
        check("A23", "the fixture log crosses the 1 MB ceiling", size > ROTATION_BYTES,
              "%d bytes" % size)

        before = run_cli(["status"], home, project).stdout

        # Rotate the same data, nothing added, nothing removed.
        rotate = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,sys;"
             "spec=importlib.util.spec_from_file_location('hs', %r);"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
             "print(m.rotate_hits())" % os.path.join(root, "scripts", "hook_stop.py")],
            capture_output=True, text=True,
        )
        check("A23", "the hook rotates a log over the ceiling", rotate.stdout.strip() == "True",
              rotate.stdout.strip() + rotate.stderr.strip())
        check("A23", "rotation produces exactly one previous generation",
              os.path.isfile(rotated) and not os.path.isfile(hits))

        after = run_cli(["status"], home, project)
        check("A23", "status still exits 0 after rotation", after.returncode == 0, after.stderr)
        check("A23", "status totals are identical across the rotation boundary",
              totals_block(after.stdout) == totals_block(before),
              "before/after differ")
        check("A23", "status names the rotated file it is also reading",
              "rotated, also counted" in after.stdout, after.stdout[-300:])

        # New hits land in a fresh live log and are counted on top of the old.
        write(hits, hits_line("strip-landed", 1) + "\n")
        third = run_cli(["status"], home, project).stdout
        old_total = [l for l in before.split("\n") if "hits across" in l]
        new_total = [l for l in third.split("\n") if "hits across" in l]
        check("A23", "post-rotation hits add to the retained history",
              bool(old_total) and bool(new_total)
              and int(new_total[0].split()[0]) == int(old_total[0].split()[0]) + 1,
              "%s -> %s" % (old_total[:1], new_total[:1]))

        # Below the ceiling, nothing moves.
        os.unlink(rotated)
        write(hits, hits_line("strip-landed", 2) + "\n")
        quiet = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util,sys;"
             "spec=importlib.util.spec_from_file_location('hs', %r);"
             "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
             "print(m.rotate_hits())" % os.path.join(root, "scripts", "hook_stop.py")],
            capture_output=True, text=True,
        )
        check("A23", "a small log is left alone",
              quiet.stdout.strip() == "False" and not os.path.isfile(rotated), quiet.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------- A24: fail open, no python3


BOUNCING_REPLY = "Landed the fix. It's worth noting that this leverages the cache."


def masked_path():
    """A PATH with the shell's own tools on it and no python3 anywhere."""
    tmp = tempfile.mkdtemp(prefix="speakingwords-nopy-")
    for tool in ("dirname", "printf", "sh", "env", "cat"):
        src = shutil.which(tool)
        if src:
            os.symlink(src, os.path.join(tmp, tool))
    return tmp


def eval_fail_open():
    home, project = make_home(), make_project()
    bindir = masked_path()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"],
                home, project)
        root = claude_root(home)
        settings = json.loads(read(os.path.join(project, ".claude", "settings.json")))
        command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]

        transcript = os.path.join(project, "transcript.jsonl")
        write(transcript, json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": BOUNCING_REPLY}]},
        }) + "\n")
        payload = json.dumps({"transcript_path": transcript})

        # Control: with a normal PATH the very same command still bounces, so a
        # clean exit below means "no interpreter", not "guard swallowed it".
        control = subprocess.run(["/bin/sh", "-c", command], input=payload,
                                 capture_output=True, text=True)
        check("A24", "the guarded hook still bounces a bad reply normally",
              control.returncode == 0 and '"block"' in control.stdout, control.stdout[:200])

        env = dict(os.environ, PATH=bindir)
        masked = subprocess.run(["/bin/sh", "-c", command], input=payload, env=env,
                                capture_output=True, text=True)
        check("A24", "python3 really is masked", shutil.which("python3", path=bindir) is None)
        check("A24", "the hook exits 0 with python3 off PATH", masked.returncode == 0,
              "exit %d, stderr %r" % (masked.returncode, masked.stderr[:200]))
        check("A24", "no reply is blocked with python3 off PATH", masked.stdout.strip() == "",
              masked.stdout[:200])
        check("A24", "the degraded hook leaves a note behind",
              os.path.isfile(os.path.join(root, "lint_disabled")))

        status = run_cli(["status"], home, project)
        check("A24", "status exits 0 on a degraded install", status.returncode == 0, status.stderr)
        check("A24", "status surfaces the degradation as a warning",
              "linting is off" in status.stdout, status.stdout[-400:])

        pref = json.loads(read(os.path.join(root, "pref.json")))
        check("A24", "the reason is recorded in pref.json",
              isinstance(pref.get("lint_disabled_reason"), str)
              and "python3" in pref["lint_disabled_reason"], json.dumps(pref))

        # Self-healing: with the interpreter back, the warning goes away again.
        healed = run_cli(["status"], home, project)
        check("A24", "the warning clears once python3 is back",
              "linting is off" not in healed.stdout, healed.stdout[-300:])
        check("A24", "the stale reason is cleared from pref.json",
              "lint_disabled_reason" not in json.loads(read(os.path.join(root, "pref.json"))))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(bindir, ignore_errors=True)


# ------------------------------------------- A25: pref forward-compatibility


# Exactly what 0.1.0 wrote: five keys, no conciseness, no reason field.
PREF_010 = {
    "agents": ["claude"],
    "mode": "hook",
    "scope": "local",
    "voice": "terse",
    "version": "0.1.0",
}


def eval_pref_forward_compat():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"],
                home, project)
        root = claude_root(home)
        pref_path = os.path.join(root, "pref.json")

        # A 0.1.0 file, plus a key only a later version would write, plus the
        # legacy `med` level a 0.2.0-dev install would have on disk. `med` is
        # not an unknown *key* — `conciseness` is one this version owns — so it
        # is the value, not the key, that has to survive a write nobody asked
        # to change the level in.
        legacy = dict(PREF_010, conciseness="med", future_thing={"nested": [1, 2]})
        write(pref_path, json.dumps(legacy, indent=2) + "\n")

        status = run_cli(["status"], home, project)
        check("A25", "status reads a 0.1.0 pref.json", status.returncode == 0, status.stderr)
        version = run_cli(["version"], home, project)
        check("A25", "version reads a 0.1.0 pref.json", version.returncode == 0, version.stderr)
        helped = run_cli(["help"], home, project)
        check("A25", "help is unaffected by pref shape", helped.returncode == 0, helped.stderr)

        updated = run_cli(["update", "less flibbertigibbet"], home, project)
        check("A25", "update runs against a 0.1.0 pref.json", updated.returncode == 0,
              updated.stderr)

        declined = run_cli(["unhook"], home, project, stdin="n\n")
        check("A25", "unhook runs against a 0.1.0 pref.json", declined.returncode == 0,
              declined.stderr)

        # Re-running init rewrites pref.json — the unknown keys must survive it.
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "convo"],
                home, project)
        after = json.loads(read(pref_path))
        check("A25", "a rewrite preserves keys this version does not know",
              after.get("future_thing") == {"nested": [1, 2]}, json.dumps(after))
        # `conciseness` is a key init owns, so a re-run writes it rather than
        # preserving it — with no flag, that is the suggested default. This is
        # not the forward-compat path; it is the line either side of it, pinned
        # so the two cannot be confused for one another.
        check("A25", "a rewrite writes the keys init owns, level included",
              after.get("conciseness") == "high", json.dumps(after))
        check("A25", "a rewrite still updates the keys it does own",
              after.get("voice") == "convo" and after.get("mode") == "hook", json.dumps(after))
        check("A25", "known keys keep their order at the front of the file",
              list(after.keys())[:5] == ["agents", "mode", "scope", "voice", "version"],
              json.dumps(list(after.keys())))

        # And the writer itself, called directly, behaves the same way. The
        # legacy level is planted again first: writePref is handed no
        # conciseness, so it must leave the value exactly as it found it. Files
        # are never rewritten to normalise a level — readers normalise at read
        # time (`med` reads as `high`), and a util that has no opinion about the
        # level does not get to have one.
        write(pref_path, json.dumps(dict(json.loads(read(pref_path)),
                                         conciseness="med"), indent=2) + "\n")
        direct = subprocess.run(
            ["node", "-e",
             "const p=require(%s);p.writePref({agents:['claude'],mode:'hook',scope:'local',"
             "voice:'terse',version:'0.2.0'});"
             "process.stdout.write(JSON.stringify(p.readPref(['claude'])));"
             % json.dumps(os.path.join(ROOT, "lib", "pref.js"))],
            env=dict(os.environ, SPEAKINGWORDS_HOME=home), capture_output=True, text=True,
        )
        written = json.loads(direct.stdout or "{}")
        check("A25", "writePref preserves a value it was not handed, legacy or not",
              written.get("conciseness") == "med" and written.get("version") == "0.2.0",
              direct.stdout + direct.stderr)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# -------------------------------------------------------------------- main


def main():
    manifest = load_manifest()

    eval_cache_parity(manifest)
    eval_cache_timing(manifest)

    eval_atomic_pref()
    eval_atomic_lexicon()
    eval_atomic_coverage()

    eval_rotation()
    eval_fail_open()
    eval_pref_forward_compat()

    drop_cache()  # leave the repo tree as it was found

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 8 deterministic evals", ""]
    for assertion in sorted(grouped):
        group = grouped[assertion]
        failed = [g for g in group if not g[1]]
        out.append(
            "%s  %s  (%d/%d)"
            % (assertion, "PASS" if not failed else "FAIL", len(group) - len(failed), len(group))
        )
        for name, ok, detail in failed:
            out.append("    FAILED: %s%s" % (name, (" — %s" % detail) if detail else ""))

    if notes:
        out.append("")
        for note in notes:
            out.append("  %s" % note)

    failures = [r for r in results if not r[2]]
    out.append("")
    out.append("%d/%d checks passed" % (len(results) - len(failures), len(results)))
    out.append("PHASE 8 PASS" if not failures else "PHASE 8 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
