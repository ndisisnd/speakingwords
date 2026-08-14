#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 9 (conciseness).

No model calls, no network. SPEAKINGWORDS_HOME fakes the home directory, so
every install path runs inside a throwaway temp tree.

What is gated here
------------------
  A19  `lint.py --conciseness` accepts low|med|high, and any other value — or
       the flag's absence entirely — behaves as `high`. Exit codes stay inside
       the A6 contract (0 or 2, never anything else) and the linter never
       crashes, whatever it is handed. `high` is the fallback because only an
       upgrade from 0.1.0 can leave the level unset, and 0.1.0 behaviour already
       measured in the `high` band (plan §8).
  A20  Every conciseness row ships at least one planted violation fixture and at
       least one clean control, and an uncovered row fails CI. The control half
       matters most: a false positive bounces a good reply, still the worst
       failure class.
  A26  The SessionStart injector emits the style block at most once per session,
       scoped to user-facing prose. Hook absence, failure and a masked
       interpreter all change nothing, and the Stop backstop is unaffected
       either way.

  P9   Plumbing: init records a level, the hook passes it to lint.py, the memory
       block states it and stays inside the 9-line budget (A1) in all four
       targets, and `update "more concise"` moves it with .bak discipline (A4).

  E8   Runner scaffold. The band arithmetic, the 20-fixture set and the
       measurement harness are all exercised here against a deterministic stub
       rewriter. The real run needs model calls and is recorded at release —
       see `e8_report()` and the --e8-stub note at the bottom of this file.

Usage:  python3 evals/run_p9.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "bin", "speakingwords.js")
SCRIPTS = os.path.join(ROOT, "skill", "scripts")
LINT = os.path.join(SCRIPTS, "lint.py")
LEXICON = os.path.join(ROOT, "skill", "refs", "lexicon.md")
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "manifest.json")

MODERN_CODEX = "0.124.0"
LEVELS = ("low", "med", "high")

# Plan §2 W2: the target cut against an unstyled reply, per level. These are
# eval targets measured across a fixture set, never per-reply rules — no linter
# can measure one reply against the reply that would otherwise have been
# written, which is exactly why E8 exists and the linter does not try.
E8_BANDS = {"low": (0.10, 0.20), "med": (0.25, 0.35), "high": (0.40, 0.50)}
E8_MIN_FIXTURES = 20
E8_MIN_WORDS = 40

# Keep lint.py importable in-process without leaving __pycache__ in the shipped
# tree, exactly as run_p8.py does.
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
import lint as lint_mod  # noqa: E402

results = []
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


def lint_run(args, text):
    proc = subprocess.run(
        [sys.executable, LINT] + args, input=text, capture_output=True, text=True
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
    return proc.returncode, payload, proc.stderr


def conciseness_rows():
    """Every row of the `## Conciseness rules` table, straight from the lexicon.

    Read from the file rather than from a list in this runner, so a row added by
    a later phase is covered by A20 the moment it lands.
    """
    text = read(LEXICON)
    section = lint_mod._section(text, lint_mod.CONCISENESS_HEADING, LEXICON, required=False)
    rows = []
    if not section:
        return rows
    for cells in lint_mod._rows(section):
        if len(cells) < 5:
            continue
        rule_id = cells[0]
        if not rule_id or rule_id.startswith("#") or rule_id.lower() == "id":
            continue
        if set(rule_id) <= set("-: "):
            continue
        rows.append((rule_id, lint_mod.parse_levels(cells[3])))
    return rows


# ------------------------------------------------- A19: the level flag itself


# Everything a user, a script or a corrupted pref.json could plausibly hand the
# flag. None of these may crash, and every one that is not a level must land on
# `high`.
BAD_LEVELS = [
    "", "medium", "bogus", "0", "-1", "low,high",
    "--voice", "'; rm -rf /", "höch", "\t", "null", "true",
]

# Values that are not exactly a level but are unambiguously one: surrounding
# whitespace and casing are normalised rather than rejected, because a level
# arriving from a shell variable or a hand-edited pref.json routinely carries
# them, and silently reading such a value as `high` would change behaviour the
# user did choose.
TOLERATED_LEVELS = {"HIGH": "high", "High": "high", " med ": "med", "high\n": "high"}

LEVEL_PROBE = (
    "To summarize, the cache is warm.\n"
    "As mentioned above, that is the whole story.\n"
    "Simply put, we are done.\n"
    "In other words, we are done.\n"
)


def eval_a19_flag():
    codes = set()

    for level in LEVELS:
        code, payload, err = lint_run(["--conciseness", level], LEVEL_PROBE)
        codes.add(code)
        check("A19", "--conciseness %s is accepted" % level,
              payload is not None and payload.get("conciseness") == level,
              "%r / %s" % (payload, err[:120]))

    for value in BAD_LEVELS:
        code, payload, err = lint_run(["--conciseness", value], LEVEL_PROBE)
        codes.add(code)
        ok = payload is not None and payload.get("conciseness") == "high"
        check("A19", "%r falls back to high" % value, ok, "%r / %s" % (payload, err[:120]))
        check("A19", "%r does not crash the linter" % value,
              payload is not None and "lint_error" not in payload, err[:160])

    for value, expected in TOLERATED_LEVELS.items():
        code, payload, err = lint_run(["--conciseness", value], LEVEL_PROBE)
        codes.add(code)
        check("A19", "%r normalises to %s" % (value, expected),
              payload is not None and payload.get("conciseness") == expected,
              "%r / %s" % (payload, err[:120]))

    # Absence of the flag entirely, and the `--conciseness` with nothing after it.
    code, payload, err = lint_run([], LEVEL_PROBE)
    codes.add(code)
    check("A19", "the flag's absence behaves as high",
          payload is not None and payload.get("conciseness") == "high", "%r" % payload)

    code, payload, err = lint_run(["--conciseness"], LEVEL_PROBE)
    codes.add(code)
    check("A19", "a trailing --conciseness with no value behaves as high",
          payload is not None and payload.get("conciseness") == "high", "%r / %s" % (payload, err[:120]))

    # The equals form, which argv parsers get wrong more often than the space form.
    code, payload, _ = lint_run(["--conciseness=low"], LEVEL_PROBE)
    codes.add(code)
    check("A19", "--conciseness=low is accepted",
          payload is not None and payload.get("conciseness") == "low", "%r" % payload)

    # A6 still holds across every one of those invocations.
    check("A19", "exit codes stay inside the A6 contract",
          codes <= {0, 2}, "observed %s" % sorted(codes))

    # And the level actually gates the rule set, which is the point of the flag.
    _, low, _ = lint_run(["--conciseness", "low"], LEVEL_PROBE)
    _, high, _ = lint_run(["--conciseness", "high"], LEVEL_PROBE)
    low_rules = {v["rule"] for v in low["violations"]}
    high_rules = {v["rule"] for v in high["violations"]}
    check("A19", "a low run fires fewer conciseness rules than a high run",
          low_rules < high_rules, "%s vs %s" % (sorted(low_rules), sorted(high_rules)))
    check("A19", "a `to summarize` fixture bounces at high and passes at low",
          "conc-to-summarize" in high_rules and "conc-to-summarize" not in low_rules,
          sorted(low_rules))

    # Strip rules are level-independent: the dial governs how much padding
    # survives, never whether a banned phrase is allowed back in.
    banned = "Landed the fix.\n"
    fired = []
    for level in LEVELS:
        _, payload, _ = lint_run(["--conciseness", level], banned)
        fired.append({v["rule"] for v in payload["violations"]} == {"strip-landed"})
    check("A19", "strip rules fire identically at every level", all(fired), str(fired))


# ------------------------------------------------------- A20: row coverage


def eval_a20_coverage():
    manifest = load_manifest()
    rows = conciseness_rows()
    check("A20", "the lexicon has a conciseness table to cover", len(rows) > 0, str(len(rows)))

    planted = {}
    for fixture, rule_ids in manifest["violations"].items():
        for rule_id in rule_ids:
            planted.setdefault(rule_id, []).append(fixture)
    controls = manifest.get("controls", {})

    uncovered_violation = [r for r, _ in rows if not planted.get(r)]
    uncovered_control = [r for r, _ in rows if not controls.get(r)]
    check("A20", "every conciseness row has a planted violation fixture",
          not uncovered_violation, ", ".join(uncovered_violation))
    check("A20", "every conciseness row has a clean control",
          not uncovered_control, ", ".join(uncovered_control))

    # A control named in the manifest has to be a real clean fixture, or the
    # coverage claim is bookkeeping rather than proof.
    stray = []
    for rule_id, names in controls.items():
        for name in names:
            if name not in manifest["clean"]:
                stray.append("%s -> %s" % (rule_id, name))
    check("A20", "every named control is in the clean set", not stray, ", ".join(stray))

    # Each planted fixture must actually trip its row, at a level the row is
    # active at, and each control must stay silent at every level.
    misses = []
    for rule_id, levels in rows:
        level = "high" if "high" in levels else sorted(levels)[0]
        for fixture in planted.get(rule_id, []):
            path = os.path.join(FIXTURES, "violations", fixture)
            _, payload, _ = lint_run(["--conciseness", level, path], "")
            if rule_id not in {v["rule"] for v in payload["violations"]}:
                misses.append("%s did not fire on %s" % (rule_id, fixture))
    check("A20", "every planted conciseness fixture trips its row", not misses,
          "; ".join(misses))

    false_positives = []
    for rule_id, names in controls.items():
        for name in names:
            path = os.path.join(FIXTURES, "clean", name)
            for level in LEVELS:
                _, payload, _ = lint_run(["--conciseness", level, path], "")
                for violation in payload["violations"]:
                    false_positives.append("%s@%s -> %s" % (name, level, violation["rule"]))
    check("A20", "no control fires a rule at any level", not false_positives,
          "; ".join(false_positives[:6]))

    # And the whole clean set stays clean at every level, not just at the
    # default. A rule that only misbehaves at `low` is still a false positive.
    dirty = []
    for name in manifest["clean"]:
        path = os.path.join(FIXTURES, "clean", name)
        for level in LEVELS:
            _, payload, _ = lint_run(["--conciseness", level, path], "")
            if payload["violations"]:
                dirty.append("%s@%s" % (name, level))
    check("A20", "the whole clean set is clean at all three levels", not dirty,
          ", ".join(dirty[:6]))

    # The `active at` column has to mean something: a row silent at every level
    # is dead code, and a row active at none of them is a parse failure.
    empty = [r for r, levels in rows if not levels]
    check("A20", "every row is active at at least one level", not empty, ", ".join(empty))


# ------------------------------------------- A26: SessionStart injection


SESSION_SCRIPT = "scripts/hook_session.py"


def session_run(root, payload, env=None, raw=None):
    script = os.path.join(root, *SESSION_SCRIPT.split("/"))
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, script], input=stdin, capture_output=True, text=True,
        env=env or dict(os.environ),
    )


def masked_path():
    """A PATH directory holding the usual tools but no python3."""
    tmp = tempfile.mkdtemp(prefix="speakingwords-nopy-")
    for tool in ("sh", "printf", "dirname", "command", "cat"):
        src = shutil.which(tool)
        if src:
            try:
                os.symlink(src, os.path.join(tmp, tool))
            except OSError:
                pass
    return tmp


def eval_a26_injection():
    home, project = make_home(), make_project()
    bindir = masked_path()
    try:
        proc = run_cli(["init", "--hook", "--agent", "claude", "--scope", "local",
                        "--voice", "convo", "--conciseness", "med"], home, project)
        if proc.returncode != 0:
            check("A26", "hook install succeeds", False, proc.stderr.strip())
            return
        root = claude_root(home)
        settings = json.loads(read(os.path.join(project, ".claude", "settings.json")))

        check("A26", "init wires a SessionStart hook",
              "SessionStart" in settings.get("hooks", {}), json.dumps(settings)[:200])
        check("A26", "the Stop hook is still wired alongside it",
              "Stop" in settings.get("hooks", {}), json.dumps(settings)[:200])
        session_command = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        check("A26", "the injector goes through the python3 guard wrapper",
              "hook_guard.sh" in session_command and "hook_session.py" in session_command,
              session_command)
        check("A26", "the injector script is installed",
              os.path.isfile(os.path.join(root, *SESSION_SCRIPT.split("/"))))

        # --- once per session ---
        first = session_run(root, {"session_id": "s-1", "source": "startup"})
        check("A26", "the first SessionStart emits a block", first.returncode == 0
              and first.stdout.strip() != "", first.stdout[:200] + first.stderr[:200])
        payload = json.loads(first.stdout)
        block = payload["hookSpecificOutput"]["additionalContext"]
        check("A26", "the output uses the SessionStart contract",
              payload["hookSpecificOutput"]["hookEventName"] == "SessionStart",
              json.dumps(payload)[:200])
        check("A26", "the block states the installed voice", "convo" in block, block)
        check("A26", "the block states the installed conciseness level", "med" in block, block)
        check("A26", "the block scopes itself to user-facing prose",
              "user-facing prose only" in block, block)
        check("A26", "the block says content may never be lost",
              "Losing" in block or "may be lost" in block, block)

        for source in ("resume", "clear", "compact", "startup"):
            again = session_run(root, {"session_id": "s-1", "source": source})
            check("A26", "a repeat SessionStart (%s) stays silent" % source,
                  again.returncode == 0 and again.stdout.strip() == "",
                  again.stdout[:120])

        other = session_run(root, {"session_id": "s-2", "source": "startup"})
        check("A26", "a different session gets its own block",
              other.returncode == 0 and other.stdout.strip() != "", other.stdout[:120])

        # --- failure modes: every one of them changes nothing ---
        for label, kwargs in (
            ("malformed stdin", {"raw": "not json at all"}),
            ("empty stdin", {"raw": ""}),
            ("a payload that is not an object", {"raw": "[1,2,3]"}),
            ("a payload with no session id", {"payload": {"source": "startup"}}),
        ):
            proc = session_run(root, kwargs.get("payload"), raw=kwargs.get("raw"))
            check("A26", "%s exits 0" % label, proc.returncode == 0, proc.stderr[:160])
            check("A26", "%s injects nothing" % label, proc.stdout.strip() == "",
                  proc.stdout[:120])

        # A missing pref.json is a degraded install, not a broken one: the block
        # still goes in, on the defaults the linter itself would use.
        pref_path = os.path.join(root, "pref.json")
        saved = read(pref_path)
        os.unlink(pref_path)
        degraded = session_run(root, {"session_id": "s-3", "source": "startup"})
        check("A26", "a missing pref.json still exits 0", degraded.returncode == 0,
              degraded.stderr[:160])
        check("A26", "a missing pref.json falls back to the high default",
              "high" in degraded.stdout, degraded.stdout[:200])
        write(pref_path, saved)

        # --- masked python3: the guard wrapper answers, nothing is emitted ---
        env = dict(os.environ, PATH=bindir)
        masked = subprocess.run(
            ["/bin/sh", "-c", session_command],
            input=json.dumps({"session_id": "s-4", "source": "startup"}),
            env=env, capture_output=True, text=True,
        )
        check("A26", "python3 really is masked", shutil.which("python3", path=bindir) is None)
        check("A26", "a masked interpreter exits 0", masked.returncode == 0,
              "exit %d, stderr %r" % (masked.returncode, masked.stderr[:160]))
        check("A26", "a masked interpreter injects nothing", masked.stdout.strip() == "",
              masked.stdout[:160])

        # --- the Stop backstop is unaffected, injected or not ---
        transcript = os.path.join(project, "transcript.jsonl")
        write(transcript, json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "Landed the fix.\n"}]},
        }) + "\n")
        stop = subprocess.run(
            [sys.executable, os.path.join(root, "scripts", "hook_stop.py")],
            input=json.dumps({"transcript_path": transcript, "stop_hook_active": False}),
            capture_output=True, text=True,
        )
        check("A26", "the Stop hook still blocks after an injection",
              stop.returncode == 0 and '"block"' in stop.stdout, stop.stdout[:160])

        # --- unhook takes the injector out with everything else ---
        removed = run_cli(["unhook", "--yes"], home, project)
        check("A26", "unhook exits 0", removed.returncode == 0, removed.stderr[:200])
        after = json.loads(read(os.path.join(project, ".claude", "settings.json")))
        check("A26", "unhook removes the SessionStart entry",
              "SessionStart" not in after.get("hooks", {}), json.dumps(after)[:200])
        check("A26", "unhook leaves no speakingwords reference behind",
              "speakingwords" not in read(os.path.join(project, ".claude", "settings.json")),
              read(os.path.join(project, ".claude", "settings.json"))[:200])
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)
        shutil.rmtree(bindir, ignore_errors=True)


# ------------------------------------------------------------- P9 plumbing


def eval_plumbing():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local",
                 "--voice", "terse", "--conciseness", "low"], home, project)
        root = claude_root(home)
        pref_path = os.path.join(root, "pref.json")
        pref = json.loads(read(pref_path))
        check("P9", "init records the chosen level", pref.get("conciseness") == "low",
              json.dumps(pref))

        # The hook reads the level and passes it through: a `to summarize` reply
        # is silent at low and blocked at high, from the same hook, same reply.
        transcript = os.path.join(project, "transcript.jsonl")
        write(transcript, json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "To summarize, it is cached.\n"}]},
        }) + "\n")

        def stop_hook():
            return subprocess.run(
                [sys.executable, os.path.join(root, "scripts", "hook_stop.py")],
                input=json.dumps({"transcript_path": transcript, "stop_hook_active": False}),
                capture_output=True, text=True,
            )

        at_low = stop_hook()
        check("P9", "the hook approves a padded reply at low",
              at_low.returncode == 0 and at_low.stdout.strip() == "", at_low.stdout[:160])

        write(pref_path, json.dumps(dict(pref, conciseness="high"), indent=2) + "\n")
        at_high = stop_hook()
        check("P9", "the same reply bounces at high",
              '"block"' in at_high.stdout and "conc-to-summarize" in at_high.stdout,
              at_high.stdout[:200])
        check("P9", "the bounce reason names the level in force",
              "high conciseness" in at_high.stdout, at_high.stdout[-200:])

        # A 0.1.0 pref.json with no key at all behaves as high, unchanged.
        legacy = {k: v for k, v in pref.items() if k != "conciseness"}
        write(pref_path, json.dumps(legacy, indent=2) + "\n")
        upgraded = stop_hook()
        check("P9", "a 0.1.0 pref.json with no level behaves as high",
              '"block"' in upgraded.stdout and "conc-to-summarize" in upgraded.stdout,
              upgraded.stdout[:200])

        # --- update moves the level, with a backup (A4) ---
        write(pref_path, json.dumps(dict(pref, conciseness="low"), indent=2) + "\n")
        moved = run_cli(["update", "more concise"], home, project)
        check("P9", "`update \"more concise\"` exits 0", moved.returncode == 0, moved.stderr[:200])
        check("P9", "`update \"more concise\"` raises the level and says so",
              json.loads(read(pref_path)).get("conciseness") == "med"
              and "Conciseness is now med" in moved.stdout, moved.stdout[:300])
        check("P9", "the level change takes a .bak first (A4)",
              os.path.isfile(pref_path + ".bak"), pref_path)
        check("P9", "the .bak holds the pre-change level",
              json.loads(read(pref_path + ".bak")).get("conciseness") == "low")

        loosened = run_cli(["update", "less aggressive"], home, project)
        check("P9", "`update \"less aggressive\"` lowers the level",
              json.loads(read(pref_path)).get("conciseness") == "low"
              and "Conciseness is now low" in loosened.stdout, loosened.stdout[:300])

        floor = run_cli(["update", "less aggressive"], home, project)
        check("P9", "the level does not fall off the bottom",
              json.loads(read(pref_path)).get("conciseness") == "low"
              and "as loose as it goes" in floor.stdout, floor.stdout[:300])

        # A level hint must not be mistaken for a rule change. "more concise"
        # reads as an allow clause to the ban/allow parser, and "less verbose"
        # reads as a ban clause — either one would put a strip rule for an
        # ordinary English word in front of every future reply.
        rule_ids = [r[0] for r in lint_mod.read_rules(
            os.path.join(root, "refs", "lexicon.md"), use_cache=False)]
        strays = [r for r in rule_ids if r.startswith("strip-user-")]
        check("P9", "a level hint adds no strip rule", not strays, ", ".join(strays))

        # `status` reports the level a user is actually getting.
        write(os.path.join(root, "hits.jsonl"), json.dumps({
            "ts": "2026-08-14T09:00:00Z", "rule": "strip-landed", "match": "Landed",
            "severity": "error", "voice": "terse",
        }) + "\n")
        status = run_cli(["status"], home, project)
        check("P9", "status names the conciseness level",
              "conciseness" in status.stdout, status.stdout[:200])
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


MEMORY_TARGETS = [
    ("claude", "local", "CLAUDE.local.md"),
    ("claude", "global", os.path.join(".claude", "CLAUDE.md")),
    ("codex", "local", "AGENTS.md"),
    ("codex", "global", os.path.join(".codex", "AGENTS.md")),
]


def eval_memory_block():
    """A1 in all four memory targets, with the level line present."""
    for agent, scope, rel in MEMORY_TARGETS:
        home, project = make_home(), make_project()
        try:
            proc = run_cli(["init", "--memory", "--agent", agent, "--scope", scope,
                            "--voice", "convo", "--conciseness", "high"], home, project)
            base = project if scope == "local" else home
            target = os.path.join(base, rel)
            label = "%s/%s" % (agent, scope)
            if proc.returncode != 0 or not os.path.isfile(target):
                check("P9", "%s: memory block written" % label, False,
                      proc.stderr.strip()[:200])
                continue
            text = read(target)
            block = text.split("<!-- speakingwords:start -->")[1].split("<!-- speakingwords:end -->")[0]
            bullets = [ln for ln in block.split("\n") if ln.startswith("- ")]
            check("A20", "%s: block stays inside the 9-line budget (A1)" % label,
                  len(bullets) <= 9, "%d lines" % len(bullets))
            check("P9", "%s: block states the conciseness level" % label,
                  any("Conciseness is high" in ln for ln in bullets), block[:300])
            check("P9", "%s: block still states the voice" % label,
                  any(ln.startswith("- Voice is ") for ln in bullets), block[:300])
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(project, ignore_errors=True)


# ---------------------------------------------------------- E8 scaffolding


def words(text):
    return len(text.split())


def reduction(before, after):
    """Fractional word-count cut. Negative means the rewrite got longer."""
    b = words(before)
    return 0.0 if b == 0 else (b - words(after)) / float(b)


def in_band(value, level):
    low, high = E8_BANDS[level]
    return low <= value <= high


def e8_fixtures(manifest):
    return [os.path.join(FIXTURES, "e8", name) for name in manifest.get("e8", [])]


def stub_rewriter(text, level):
    """A deterministic stand-in for the model, used to exercise the harness.

    It drops whole words to land in the middle of the band. It is not a rewrite
    and proves nothing about the skill — it proves the measurement plumbing, so
    that a real recorded run is measuring what it thinks it is measuring.
    """
    target = sum(E8_BANDS[level]) / 2.0
    tokens = text.split()
    keep = max(1, int(round(len(tokens) * (1.0 - target))))
    return " ".join(tokens[:keep])


def e8_measure(paths, rewrite):
    """Median reduction per level for a rewrite callable.

    `rewrite(text, level) -> str`. The real runner passes a model call here; the
    deterministic gate below passes `stub_rewriter`.
    """
    out = {}
    for level in LEVELS:
        cuts = []
        for path in paths:
            before = read(path)
            cuts.append(reduction(before, rewrite(before, level)))
        out[level] = statistics.median(cuts)
    return out


def eval_e8_scaffold():
    manifest = load_manifest()
    paths = e8_fixtures(manifest)

    check("E8", "the fixture set has at least %d replies" % E8_MIN_FIXTURES,
          len(paths) >= E8_MIN_FIXTURES, str(len(paths)))
    missing = [p for p in paths if not os.path.isfile(p)]
    check("E8", "every listed fixture exists", not missing,
          ", ".join(os.path.basename(p) for p in missing[:6]))

    short = [os.path.basename(p) for p in paths
             if os.path.isfile(p) and words(read(p)) < E8_MIN_WORDS]
    check("E8", "every fixture is long enough for a 50%% cut to be measurable",
          not short, ", ".join(short[:6]))

    # Band arithmetic, checked directly rather than trusted.
    check("E8", "band arithmetic accepts a mid-band cut",
          all(in_band(sum(E8_BANDS[l]) / 2.0, l) for l in LEVELS))
    check("E8", "band arithmetic rejects an out-of-band cut",
          not in_band(0.05, "low") and not in_band(0.60, "high")
          and not in_band(0.20, "med"))
    check("E8", "the bands do not overlap",
          E8_BANDS["low"][1] < E8_BANDS["med"][0] < E8_BANDS["med"][1] < E8_BANDS["high"][0])

    if not missing and paths:
        medians = e8_measure(paths, stub_rewriter)
        off = [l for l in LEVELS if not in_band(medians[l], l)]
        check("E8", "the measurement harness lands the stub in band at every level",
              not off, ", ".join("%s=%.3f" % (l, medians[l]) for l in off))
        notes.append("E8 stub medians: " + ", ".join(
            "%s %.1f%%" % (l, medians[l] * 100) for l in LEVELS))

    # The convo guardrail the bands must not break: `high` is not permission to
    # collapse convo into terse. Checked here on the linter, which is the part
    # that can be checked without a model — E4 re-runs the judged half.
    prose = "\n".join([
        "The retry budget is three attempts.",
        "A fourth failure parks the job in the dead-letter queue.",
        "It waits there for a human.",
    ])
    _, payload, _ = lint_run(["--voice", "convo", "--conciseness", "high"], prose)
    check("E8", "high + convo does not trip the terse prose-block rule",
          "terse-prose-block" not in {v["rule"] for v in payload["violations"]},
          json.dumps(payload)[:200])


def e8_report():
    """How the judged half of E8 gets recorded at release.

    Deliberately not run here: it costs model calls, and this file is part of
    `eval:deterministic`. The recorded run is:

      1. For each of the 20 fixtures in `manifest["e8"]`, and each level, ask the
         installed agent to rewrite the fixture under skill/SKILL.md at that
         level. That is the `rewrite` callable e8_measure() takes.
      2. Feed the results to e8_measure() and check every median with in_band().
      3. Send the same before/after pairs to an LLM judge asking one question per
         pair: does every fact, number, path and code block in the original still
         appear in the rewrite? The gate is zero losses, not a percentage — the
         anti-loss invariant outranks the bands. One thing is not a loss, and the
         judge is told so: an enumeration the reader can retrieve elsewhere (the
         files in a diff, the rules in a lexicon table) collapsed to its function,
         its count and a pointer. Dropping the count or the pointer is a loss, and
         so is dropping anything the pointer does not carry. See "What counts as a
         fact" in skill/SKILL.md.
      4. Re-run the E4 "convo never collapses into terse" gate at every level.
      5. Paste medians, the judge's verdict and the model id into the release
         notes, as E2-E5 are recorded.

    Everything deterministic about that pipeline — the fixture set, the band
    arithmetic, the measurement, the convo guardrail on the linter side — is
    gated above and passes now.
    """
    return __doc__


# -------------------------------------------------------------------- main


def main():
    eval_a19_flag()
    eval_a20_coverage()
    eval_a26_injection()
    eval_plumbing()
    eval_memory_block()
    eval_e8_scaffold()

    try:
        os.unlink(lint_mod.cache_path(LEXICON))  # leave the repo tree as found
    except OSError:
        pass

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 9 deterministic evals", ""]
    for assertion in sorted(grouped):
        group = grouped[assertion]
        failed = [(n, d) for n, ok, d in group if not ok]
        out.append("%-4s %s  (%d/%d)" % (
            assertion, "PASS" if not failed else "FAIL",
            len(group) - len(failed), len(group)))
        for name, detail in failed:
            out.append("       FAILED: %s%s" % (name, (" — %s" % detail) if detail else ""))

    if notes:
        out.append("")
        for note in notes:
            out.append("  %s" % note)

    failures = [r for r in results if not r[2]]
    out.append("")
    out.append("%d/%d checks passed" % (len(results) - len(failures), len(results)))
    out.append("PHASE 9 PASS" if not failures else "PHASE 9 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
