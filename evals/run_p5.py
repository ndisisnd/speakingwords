#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 5 (the utils).

No model calls, no network, nothing outside a throwaway temp tree.
SPEAKINGWORDS_HOME fakes the home directory and SPEAKINGWORDS_CODEX_VERSION
fakes the installed Codex, so every path here runs on a machine that has
neither agent installed.

What is gated here
------------------
  A3   `unhook` leaves zero "speakingwords" references in settings.json,
       hooks.json or config.toml, and says so in its own post-check.
  A4   `update` never edits without a .bak beside every touched file. When the
       backup cannot be written, the original is left untouched.
  A7   `version` output == package.json version == pref.json version.
  A9   `status` skips and counts malformed hits.jsonl lines instead of crashing.
  A10  `status` under memory mode exits 0 with the explanatory line.

Plus the behaviour those rest on: hit aggregation and ordering, the empty-log
path, the lint integration that proves an `update` edit is actually enforced,
the decline path, and the `unset` alias.

Usage:  python3 evals/run_p5.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "bin", "speakingwords.js")
HOOKS_LIB = os.path.join(ROOT, "lib", "hooks.js")
PKG = os.path.join(ROOT, "package.json")
REPO_LEXICON = os.path.join(ROOT, "skill", "refs", "lexicon.md")

MODERN_CODEX = "0.124.0"

# Nonsense words on purpose: no seeded rule can match them, so a hit proves the
# `update` edit did it.
BAN_WORD = "flibbertigibbet"
BAN_WORD_2 = "zorkmid"

results = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def run_cli(args, home, cwd, stdin="", codex_version=MODERN_CODEX):
    env = dict(os.environ, SPEAKINGWORDS_HOME=home)
    if codex_version is None:
        env.pop("SPEAKINGWORDS_CODEX_VERSION", None)
    else:
        env["SPEAKINGWORDS_CODEX_VERSION"] = codex_version
    return subprocess.run(
        ["node", CLI] + args, cwd=cwd, env=env, input=stdin, capture_output=True, text=True
    )


def run_node(script, home, cwd):
    proc = subprocess.run(
        ["node", "-e", "const h=require(%s);%s" % (json.dumps(HOOKS_LIB), script)],
        cwd=cwd,
        env=dict(os.environ, SPEAKINGWORDS_HOME=home),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()}
    return json.loads(proc.stdout or "null")


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


def ago(**kwargs):
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(**kwargs)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def hit(rule, match, severity="error", **extra):
    record = {"ts": ago(hours=1), "rule": rule, "match": match, "severity": severity, "voice": "terse"}
    record.update(extra)
    return json.dumps(record)


def table_order(out, *rules):
    """Positions of each rule id in the rendered table, in output order."""
    return [out.find(r) for r in rules]


# ------------------------------------------------------- status: the table


def eval_status_table():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        root = claude_root(home)
        write(
            os.path.join(root, "hits.jsonl"),
            "\n".join(
                [
                    hit("strip-landed", "Landed"),
                    hit("strip-landed", "Landed"),
                    json.dumps({"ts": ago(minutes=5), "rule": "strip-landed", "match": "Landed",
                                "severity": "error", "voice": "terse"}),
                    hit("strip-great-point", "great point", severity="error"),
                ]
            )
            + "\n",
        )

        proc = run_cli(["status"], home, project)
        out = proc.stdout
        check("P5", "status exits 0 with a hit table", proc.returncode == 0, proc.stderr.strip())
        check("P5", "status counts each rule", "strip-landed" in out and "strip-great-point" in out, out)

        landed_row = [l for l in out.split("\n") if l.startswith("strip-landed")]
        point_row = [l for l in out.split("\n") if l.startswith("strip-great-point")]
        check("P5", "status reports the right count per rule",
              bool(landed_row) and landed_row[0].split()[1] == "3",
              landed_row[0] if landed_row else "no row")
        check("P5", "status reports the second rule's count",
              bool(point_row) and point_row[0].split()[1] == "1",
              point_row[0] if point_row else "no row")

        positions = table_order(out, "strip-landed", "strip-great-point")
        check("P5", "status sorts by hit count, loudest first",
              positions[0] != -1 and positions[0] < positions[1], str(positions))

        check("P5", "status shows an example of the matched phrase",
              "Landed" in out and "great point" in out, out)
        check("P5", "status humanises the last-seen time",
              "m ago" in out or "h ago" in out or "just now" in out, out)
        check("P5", "status summarises the total and the span",
              "4 hits across 2 rules" in out and "spanning" in out, out)
        check("P5", "a log with no audit records says nothing about audits",
              "audit-only" not in out, out)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# --------------------------------------------------- status: A9 and A10


def eval_status_malformed():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        write(
            os.path.join(claude_root(home), "hits.jsonl"),
            "\n".join(
                [
                    hit("strip-landed", "Landed"),
                    "{not json at all",
                    hit("strip-delve", "delving"),
                    '{"ts":"2026-01-01T00:00:00Z"',  # truncated line, as a killed process leaves
                    "[1,2,3]",  # valid JSON, wrong shape
                    hit("strip-landed", "Landed"),
                ]
            )
            + "\n",
        )

        proc = run_cli(["status"], home, project)
        out = proc.stdout
        check("A9", "status survives malformed lines and exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A9", "status reports how many lines it skipped", "3 malformed lines skipped" in out, out)
        check("A9", "status still counts every well-formed line",
              "3 hits across 2 rules" in out, out)
        check("A9", "no stack trace reaches the user", "Error" not in proc.stderr, proc.stderr.strip())
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_status_memory_mode():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--memory", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        proc = run_cli(["status"], home, project)
        out = proc.stdout
        check("A10", "status under memory mode exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A10", "status explains why there is nothing to count",
              "nothing to count" in out and "hook mode records every catch" in out, out)
        check("A10", "no stack trace under memory mode",
              "at Object" not in proc.stderr and proc.stderr.strip() == "", proc.stderr.strip())
        check("A10", "no table is printed when there is no telemetry", "RULE" not in out, out)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_status_empty_log():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        proc = run_cli(["status"], home, project)
        check("A10", "an absent hits.jsonl is friendly, not fatal",
              proc.returncode == 0 and "No hits recorded yet" in proc.stdout, proc.stdout)

        write(os.path.join(claude_root(home), "hits.jsonl"), "")
        proc = run_cli(["status"], home, project)
        check("A10", "an empty hits.jsonl is friendly too",
              proc.returncode == 0 and "No hits recorded yet" in proc.stdout, proc.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_status_audit_split():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        write(
            os.path.join(claude_root(home), "hits.jsonl"),
            "\n".join(
                [
                    hit("strip-landed", "Landed"),
                    hit("strip-delve", "delve", audit=True, agent="codex"),
                    hit("strip-delve", "delve", audit=True, agent="codex"),
                ]
            )
            + "\n",
        )
        proc = run_cli(["status"], home, project)
        out = proc.stdout
        check("P5", "status separates enforced bounces from audit-only records",
              "1 enforced (bounced), 2 audit-only" in out, out)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------------------ update: A4


def lint_verdict(skill_root, text, voice="convo"):
    """Run the INSTALLED lint.py over text — the same file the hook runs."""
    proc = subprocess.run(
        [sys.executable, os.path.join(skill_root, "scripts", "lint.py"), "--voice", voice],
        input=text,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


def eval_update_ban_and_allow():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        root = claude_root(home)
        lexicon = os.path.join(root, "refs", "lexicon.md")
        fixture = "The %s was reviewed today.\n" % BAN_WORD

        code, _ = lint_verdict(root, fixture)
        check("P5", "the phrase is clean before the update", code == 0, str(code))

        proc = run_cli(["update", "no %s" % BAN_WORD], home, project)
        out = proc.stdout
        check("A4", "update exits 0 on a recognised hint", proc.returncode == 0, proc.stderr.strip())
        check("A4", "a .bak sits beside the edited lexicon",
              os.path.exists(lexicon + ".bak"), lexicon + ".bak")
        check("A4", "the .bak holds the pre-edit content",
              BAN_WORD not in read(lexicon + ".bak"), "backup already contains the new rule")
        check("A4", "update prints the backup path", ".bak" in out, out)

        text = read(lexicon)
        check("P5", "the new rule row is in the strip table",
              "| strip-user-%s |" % BAN_WORD in text, "row missing")
        check("P5", "the new rule is word-bounded", "\\b%s\\b" % BAN_WORD in text, "pattern missing")
        check("P5", "update prints a diff-style summary",
              "+ strip-user-%s" % BAN_WORD in out, out)

        code, verdict = lint_verdict(root, fixture)
        check("P5", "lint.py now catches the banned phrase (integration)",
              code == 2 and "strip-user-%s" % BAN_WORD in verdict, "%s %s" % (code, verdict.strip()))

        # --- and back out again ---
        proc = run_cli(["update", "more %s" % BAN_WORD], home, project)
        out = proc.stdout
        check("P5", "an allow hint exits 0", proc.returncode == 0, proc.stderr.strip())
        check("P5", "the rule row is gone",
              "| strip-user-%s |" % BAN_WORD not in read(lexicon), "row survived")
        check("P5", "update prints the removal in the summary",
              "- strip-user-%s" % BAN_WORD in out, out)

        code, _ = lint_verdict(root, fixture)
        check("P5", "lint.py no longer flags the phrase", code == 0, str(code))

        # A seeded rule can be allowed back too, not just user-added ones.
        proc = run_cli(["update", "allow robust"], home, project)
        check("P5", "an allow hint removes a seeded rule as well",
              proc.returncode == 0 and "strip-robust" not in read(lexicon), proc.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_update_unrecognised():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        lexicon = os.path.join(claude_root(home), "refs", "lexicon.md")
        before = read(lexicon)

        proc = run_cli(["update", "make it sound nicer please"], home, project)
        check("A4", "an unrecognised hint exits 1", proc.returncode == 1, proc.stdout)
        check("A4", "an unrecognised hint edits nothing", read(lexicon) == before, "lexicon changed")
        check("A4", "an unrecognised hint writes no .bak",
              not os.path.exists(lexicon + ".bak"), "stray backup")
        check("A4", "an unrecognised hint says what a hint looks like",
              "less" in proc.stderr and "more" in proc.stderr, proc.stderr.strip())

        proc = run_cli(["update"], home, project)
        check("A4", "update with no hint exits 1 and prints usage",
              proc.returncode == 1 and "Usage:" in proc.stderr, proc.stderr.strip())
        check("A4", "update with no hint edits nothing", read(lexicon) == before, "lexicon changed")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_update_backup_failure():
    """A4: if the .bak cannot be written, the original must be untouched."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        check("A4", "backup failure aborts the edit (skipped: running as root)", True, "skipped")
        return

    home, project = make_home(), make_project()
    refs = os.path.join(claude_root(home), "refs")
    try:
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        lexicon = os.path.join(refs, "lexicon.md")
        before = read(lexicon)

        os.chmod(refs, stat.S_IRUSR | stat.S_IXUSR)  # read-only directory
        proc = run_cli(["update", "no %s" % BAN_WORD_2], home, project)
        os.chmod(refs, stat.S_IRWXU)

        check("A4", "a failed backup aborts with exit 1", proc.returncode == 1, proc.stdout)
        check("A4", "a failed backup leaves the original untouched",
              read(lexicon) == before, "lexicon was edited anyway")
        check("A4", "a failed backup leaves no partial .bak",
              not os.path.exists(lexicon + ".bak"), "stray backup")
        check("A4", "a failed backup says nothing was edited",
              "Nothing was edited" in proc.stderr, proc.stderr.strip())
    finally:
        try:
            os.chmod(refs, stat.S_IRWXU)
        except OSError:
            pass
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_update_memory_mode():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--memory", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        memory_file = os.path.join(project, "CLAUDE.local.md")
        check("P5", "memory mode wrote its block before the update",
              os.path.exists(memory_file), memory_file)

        repo_before = read(REPO_LEXICON)
        proc = run_cli(["update", "no %s" % BAN_WORD], home, project)
        out = proc.stdout
        check("P5", "memory-mode update exits 0", proc.returncode == 0, proc.stderr.strip())
        check("P5", "memory-mode update never edits the repo checkout",
              read(REPO_LEXICON) == repo_before and not os.path.exists(REPO_LEXICON + ".bak"),
              "repo lexicon touched")

        check("A4", "the memory file gets its own .bak",
              os.path.exists(memory_file + ".bak"), memory_file + ".bak")
        check("A4", "the memory .bak holds the pre-edit block",
              BAN_WORD not in read(memory_file + ".bak"), "backup already updated")

        block = read(memory_file)
        check("P5", "the memory block is re-rendered with the new rule",
              BAN_WORD in block, "block not re-rendered")
        check("P5", "the marker block still appears exactly once",
              block.count("<!-- speakingwords:start -->") == 1, str(block.count("<!-- speakingwords:start -->")))
        check("A1", "the re-rendered block is still within 9 bullet lines",
              len([l for l in block.split("\n") if l.startswith("- ")]) <= 9, block)
        check("P5", "update says it re-rendered the memory block",
              "memory block re-rendered" in out, out)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------------------- version: A7


def eval_version():
    home, project = make_home(), make_project()
    try:
        pkg_version = json.loads(read(PKG))["version"]
        run_cli(["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        pref = json.loads(read(os.path.join(claude_root(home), "pref.json")))
        proc = run_cli(["version"], home, project)
        printed = proc.stdout.strip()

        check("A7", "version exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A7", "printed version == package.json version",
              printed == pkg_version, "%s vs %s" % (printed, pkg_version))
        check("A7", "pref.json version == package.json version",
              pref.get("version") == pkg_version, "%s vs %s" % (pref.get("version"), pkg_version))

        proc = run_cli(["--version"], home, project)
        check("A7", "the --version flag prints the same string",
              proc.stdout.strip() == pkg_version, proc.stdout.strip())
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# -------------------------------------------------------------- unhook: A3

SETTINGS_SEED = {
    "env": {"EDITOR": "vim"},
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /opt/mine/guard.py"}]}
        ]
    },
}

HOOKS_SEED = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /opt/mine/codexguard.py"}]}
    ]
}

CONFIG_SEED = 'model = "gpt-5"\n\n[tui]\ntheme = "dark"\n'


def seed_both_agents(home, project):
    """A both-agents hook install on top of user content in all three files."""
    settings = os.path.join(project, ".claude", "settings.json")
    hooks_json = os.path.join(project, ".codex", "hooks.json")
    config = os.path.join(home, ".codex", "config.toml")

    write(settings, json.dumps(SETTINGS_SEED, indent=2) + "\n")
    write(hooks_json, json.dumps(HOOKS_SEED, indent=2) + "\n")
    write(config, CONFIG_SEED)

    proc = run_cli(
        ["init", "--hook", "--agent", "both", "--scope", "local", "--voice", "terse"], home, project
    )
    # The modern-Codex path wires hooks.json only, so the notify line is added
    # explicitly to prove unhook clears config.toml as well (A3 names all three).
    run_node(
        "process.stdout.write(JSON.stringify(h.installCodexNotify({skillRoot:h.coreRoot(['claude','codex'])})));",
        home,
        project,
    )
    write(os.path.join(claude_root(home), "hits.jsonl"), hit("strip-landed", "Landed") + "\n")
    return proc, settings, hooks_json, config


def eval_unhook_decline():
    home, project = make_home(), make_project()
    try:
        _, settings, hooks_json, config = seed_both_agents(home, project)
        before = [read(f) for f in (settings, hooks_json, config)]

        proc = run_cli(["unhook"], home, project, stdin="n\n")
        out = proc.stdout
        check("A3", "declining exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A3", "declining says nothing changed", "Nothing changed" in out, out)
        check("A3", "the warning lists every file it would touch",
              settings in out and hooks_json in out and config in out, out)
        check("A3", "the warning states that telemetry is kept",
              "hits.jsonl" in out and "Kept" in out, out)
        check("A3", "declining leaves every config byte-identical",
              [read(f) for f in (settings, hooks_json, config)] == before, "a config changed")

        pref = json.loads(read(os.path.join(claude_root(home), "pref.json")))
        check("A3", "declining leaves pref mode alone", pref["mode"] == "hook", json.dumps(pref))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_unhook_confirm(verb="unhook"):
    home, project = make_home(), make_project()
    try:
        _, settings, hooks_json, config = seed_both_agents(home, project)
        hits = os.path.join(claude_root(home), "hits.jsonl")

        check("A3", "[%s] the install did wire all three files" % verb,
              "speakingwords" in read(settings)
              and "speakingwords" in read(hooks_json)
              and "speakingwords" in read(config),
              "wiring missing before removal")

        proc = run_cli([verb, "--yes"], home, project)
        out = proc.stdout
        check("A3", "[%s] --yes needs no prompt and exits 0" % verb,
              proc.returncode == 0, proc.stderr.strip())

        remaining = 0
        for f in (settings, hooks_json, config):
            if os.path.exists(f):
                remaining += read(f).count("speakingwords")
        check("A3", "[%s] zero speakingwords references remain in any config" % verb,
              remaining == 0, "%d references" % remaining)
        check("A3", "[%s] the command prints its own post-check" % verb,
              "post-check: 0 references remain" in out, out)

        check("A3", "[%s] unrelated settings.json content is byte-identical" % verb,
              read(settings) == json.dumps(SETTINGS_SEED, indent=2) + "\n", read(settings))
        check("A3", "[%s] unrelated hooks.json content is byte-identical" % verb,
              read(hooks_json) == json.dumps(HOOKS_SEED, indent=2) + "\n", read(hooks_json))
        check("A3", "[%s] unrelated config.toml content is byte-identical" % verb,
              read(config) == CONFIG_SEED, read(config))

        check("A3", "[%s] hits.jsonl is kept" % verb, os.path.exists(hits), hits)
        check("A3", "[%s] the installed skill files are kept" % verb,
              os.path.exists(os.path.join(claude_root(home), "scripts", "lint.py")), "skill core removed")

        pref = json.loads(read(os.path.join(claude_root(home), "pref.json")))
        check("A3", "[%s] pref mode becomes unhooked" % verb, pref["mode"] == "unhooked", json.dumps(pref))
        check("A3", "[%s] pref keeps voice and agents for a later reinstall" % verb,
              pref["voice"] == "terse" and pref["agents"] == ["claude", "codex"], json.dumps(pref))

        # Second run must be honest rather than pretending to remove again.
        proc = run_cli([verb, "--yes"], home, project)
        check("A3", "[%s] a second run says it is already unhooked" % verb,
              proc.returncode == 0 and "already unhooked" in proc.stdout, proc.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_unhook_memory_mode():
    home, project = make_home(), make_project()
    try:
        run_cli(["init", "--memory", "--agent", "claude", "--scope", "local", "--voice", "terse"], home, project)
        memory_file = os.path.join(project, "CLAUDE.local.md")
        before = read(memory_file)

        proc = run_cli(["unhook"], home, project)
        out = proc.stdout
        check("A3", "memory-mode unhook exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A3", "memory-mode unhook explains that it only removes hooks",
              "memory mode" in out and "only removes hook wiring" in out, out)
        check("A3", "memory-mode unhook points at the manual removal",
              "<!-- speakingwords:start -->" in out and memory_file in out, out)
        check("A3", "memory-mode unhook changes nothing", read(memory_file) == before, "block changed")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# -------------------------------------------------------------------- main


def main():
    eval_status_table()
    eval_status_malformed()
    eval_status_memory_mode()
    eval_status_empty_log()
    eval_status_audit_split()

    eval_update_ban_and_allow()
    eval_update_unrecognised()
    eval_update_backup_failure()
    eval_update_memory_mode()

    eval_version()

    eval_unhook_decline()
    eval_unhook_confirm("unhook")
    eval_unhook_confirm("unset")
    eval_unhook_memory_mode()

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 5 deterministic evals", ""]
    for assertion in sorted(grouped):
        group = grouped[assertion]
        failed = [g for g in group if not g[1]]
        out.append(
            "%s  %s  (%d/%d)"
            % (assertion, "PASS" if not failed else "FAIL", len(group) - len(failed), len(group))
        )
        for name, ok, detail in failed:
            out.append("    FAILED: %s%s" % (name, (" — %s" % detail) if detail else ""))

    failures = [r for r in results if not r[2]]
    out.append("")
    out.append("%d/%d checks passed" % (len(results) - len(failures), len(results)))
    out.append("PHASE 5 PASS" if not failures else "PHASE 5 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
