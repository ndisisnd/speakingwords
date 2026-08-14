#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 3 (hook mode, Claude Code).

No model calls, no network, nothing outside a throwaway temp tree:
SPEAKINGWORDS_HOME fakes the home directory and a temp cwd fakes the project,
so the real ~/.claude is never touched.

What is gated here
------------------
  A5  Loop guard. A violating reply bounces exactly once. With
      stop_hook_active true the hook approves silently, so a rewrite can
      always land.
  A6  Exit-code contract. lint.py exits 0 on clean input and 2 on violations,
      never anything else — the hook wiring depends on it. The hook itself
      always exits 0, whatever it is handed.
  A9  Every hits.jsonl line is valid single-line JSON.
  A2  Settings merges are idempotent, and install -> uninstall restores a
      pre-existing settings.json byte-for-byte.
  Fail-open. Malformed stdin and a missing transcript both approve.

Usage:  python3 evals/run_p3.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "bin", "speakingwords.js")
LINT = os.path.join(ROOT, "skill", "scripts", "lint.py")
FIXTURES = os.path.join(HERE, "fixtures")

VIOLATING_REPLY = (
    "Landed the retry fix on the worker queue.\n"
    "\n"
    "You're absolutely right that the backoff was too aggressive. I hope this helps!\n"
)
CLEAN_REPLY = (
    "- Retry count is now three, with a 2s backoff.\n"
    "- Failures past that go to the dead-letter table.\n"
)

results = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def run_cli(args, home, cwd):
    env = dict(os.environ, SPEAKINGWORDS_HOME=home)
    return subprocess.run(
        ["node", CLI] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def write_transcript(path, reply_text, trailing_user=False):
    """A minimal Claude Code JSONL transcript ending in one assistant turn."""
    lines = [
        {"type": "user", "message": {"role": "user", "content": "why is the queue retrying?"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": reply_text}],
            },
        },
    ]
    if trailing_user:
        lines.insert(0, {"type": "user", "message": {"role": "user", "content": "hi"}})
    with open(path, "w", encoding="utf-8") as fh:
        for entry in lines:
            fh.write(json.dumps(entry) + "\n")


def run_hook(skill_root, payload, raw=None):
    script = os.path.join(skill_root, "scripts", "hook_stop.py")
    stdin = raw if raw is not None else json.dumps(payload)
    return subprocess.run(
        [sys.executable, script],
        input=stdin,
        capture_output=True,
        text=True,
    )


def hits_lines(skill_root):
    path = os.path.join(skill_root, "hits.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [ln for ln in fh.read().split("\n") if ln.strip()]


def expected_rule_ids(text, voice):
    """Ask lint.py directly what it finds, so the hook is checked against the
    linter rather than against a hand-copied list of rule ids."""
    proc = subprocess.run(
        [sys.executable, LINT, "--voice", voice],
        input=text,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    return proc.returncode, [v["rule"] for v in payload["violations"]]


# ----------------------------------------------------------- hook behaviour


def eval_hook(voice="terse"):
    home = tempfile.mkdtemp(prefix="speakingwords-home-")
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    try:
        proc = run_cli(
            ["init", "--hook", "--agent", "claude", "--scope", "local", "--voice", voice],
            home,
            project,
        )
        if proc.returncode != 0:
            check("P3", "hook install succeeds", False, proc.stderr.strip())
            return
        skill_root = os.path.join(home, ".claude", "skills", "speakingwords")

        # The hook command path must actually exist, or every reply fails open
        # forever and nothing is ever enforced.
        for rel in ("SKILL.md", "refs/lexicon.md", "scripts/lint.py", "scripts/hook_stop.py", "pref.json"):
            check(
                "P3",
                "installed core has %s" % rel,
                os.path.isfile(os.path.join(skill_root, *rel.split("/"))),
            )
        check(
            "P3",
            "skill core is byte-identical to the repo copy",
            open(os.path.join(skill_root, "SKILL.md"), "rb").read()
            == open(os.path.join(ROOT, "skill", "SKILL.md"), "rb").read(),
        )
        pref = json.load(open(os.path.join(skill_root, "pref.json"), "r", encoding="utf-8"))
        check("P3", "pref.json records hook mode", pref.get("mode") == "hook", json.dumps(pref))
        check("P3", "pref.json records the agent", pref.get("agents") == ["claude"])
        check("P3", "pref.json records the voice", pref.get("voice") == voice)
        check(
            "P3",
            "hook mode writes no memory block",
            not os.path.exists(os.path.join(project, "CLAUDE.local.md")),
        )

        transcript = os.path.join(project, "transcript.jsonl")

        # --- (a) violating reply, guard down -> bounce ---
        write_transcript(transcript, VIOLATING_REPLY)
        proc = run_hook(
            skill_root,
            {
                "hook_event_name": "Stop",
                "transcript_path": transcript,
                "stop_hook_active": False,
            },
        )
        check("P3", "(a) violating reply: hook exits 0", proc.returncode == 0, proc.stderr.strip())
        decision = None
        try:
            decision = json.loads(proc.stdout)
        except ValueError:
            pass
        check("P3", "(a) violating reply: stdout is one JSON object", isinstance(decision, dict), proc.stdout[:200])
        if isinstance(decision, dict):
            check("A5", "(a) decision is block", decision.get("decision") == "block")
            reason = decision.get("reason", "")
            code, rule_ids = expected_rule_ids(VIOLATING_REPLY, voice)
            check("A6", "lint.py exits 2 on the violating fixture", code == 2, "exit %s" % code)
            check(
                "P3",
                "(a) reason names every rule id the linter found",
                rule_ids and all(rid in reason for rid in rule_ids),
                "missing: %s" % [r for r in rule_ids if r not in reason],
            )
            check(
                "P3",
                "(a) reason quotes the matched text",
                "Landed" in reason,
                reason[:200],
            )
            check(
                "P3",
                "(a) reason instructs a rewrite against SKILL.md in the installed voice",
                "Rewrite the last reply" in reason
                and os.path.join(skill_root, "SKILL.md") in reason
                and ("%s voice" % voice) in reason,
                reason[-200:],
            )
            check(
                "P3",
                "(a) reason tells the agent not to mention the correction",
                "Do not mention the correction" in reason,
            )
            check("P3", "(a) reason stays bounded", len(reason) <= 4000, "%d chars" % len(reason))

        after_bounce = hits_lines(skill_root)
        check("P3", "(a) bounce logged to hits.jsonl", len(after_bounce) > 0, "%d lines" % len(after_bounce))
        for i, line in enumerate(after_bounce):
            try:
                record = json.loads(line)
            except ValueError as exc:
                check("A9", "hits.jsonl line %d is valid JSON" % i, False, str(exc))
                continue
            check(
                "A9",
                "hits.jsonl line %d is a single-line record with ts/rule/match" % i,
                "\n" not in line
                and isinstance(record.get("ts"), str)
                and record.get("ts").endswith("Z")
                and "T" in record.get("ts")
                and isinstance(record.get("rule"), str)
                and "match" in record,
                line[:160],
            )

        # --- (b) same reply, guard up -> silence (A5) ---
        proc = run_hook(
            skill_root,
            {
                "hook_event_name": "Stop",
                "transcript_path": transcript,
                "stop_hook_active": True,
            },
        )
        check("A5", "(b) second pass exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A5", "(b) second pass never blocks", proc.stdout.strip() == "", proc.stdout[:200])
        check(
            "A5",
            "(b) suppressed pass is not double-counted in hits.jsonl",
            hits_lines(skill_root) == after_bounce,
        )

        # --- (c) clean reply -> silence, no telemetry ---
        write_transcript(transcript, CLEAN_REPLY)
        code, rule_ids = expected_rule_ids(CLEAN_REPLY, voice)
        check("A6", "lint.py exits 0 on the clean fixture", code == 0, "exit %s, %s" % (code, rule_ids))
        proc = run_hook(
            skill_root,
            {
                "hook_event_name": "Stop",
                "transcript_path": transcript,
                "stop_hook_active": False,
            },
        )
        check("P3", "(c) clean reply: exits 0", proc.returncode == 0, proc.stderr.strip())
        check("P3", "(c) clean reply: no output at all", proc.stdout == "", proc.stdout[:200])
        check("P3", "(c) clean reply: hits.jsonl does not grow", hits_lines(skill_root) == after_bounce)

        # --- (d) malformed stdin -> fail open ---
        proc = run_hook(skill_root, None, raw="{not json at all")
        check("P3", "(d) malformed stdin: exits 0", proc.returncode == 0)
        check("P3", "(d) malformed stdin: no block", proc.stdout.strip() == "", proc.stdout[:200])
        proc = run_hook(skill_root, None, raw="")
        check("P3", "(d) empty stdin: exits 0 and no block", proc.returncode == 0 and proc.stdout.strip() == "")

        # --- (e) missing transcript -> fail open ---
        proc = run_hook(
            skill_root,
            {
                "hook_event_name": "Stop",
                "transcript_path": os.path.join(project, "nope.jsonl"),
                "stop_hook_active": False,
            },
        )
        check("P3", "(e) missing transcript: exits 0", proc.returncode == 0)
        check("P3", "(e) missing transcript: no block", proc.stdout.strip() == "", proc.stdout[:200])
        proc = run_hook(skill_root, {"hook_event_name": "Stop"})
        check("P3", "(e) absent transcript_path: exits 0 and no block",
              proc.returncode == 0 and proc.stdout.strip() == "")

        # --- corrupt transcript lines must not sink the pass ---
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.write("{ half a line\n")
            fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant",
                     "content": [{"type": "text", "text": VIOLATING_REPLY}]}}) + "\n")
        proc = run_hook(
            skill_root,
            {"hook_event_name": "Stop", "transcript_path": transcript, "stop_hook_active": False},
        )
        check(
            "P3",
            "(f) unparseable transcript line skipped, reply still linted",
            proc.returncode == 0 and '"block"' in proc.stdout,
            proc.stdout[:200],
        )

        check("P3", "hits.jsonl lives beside pref.json in the skill root",
              os.path.isfile(os.path.join(skill_root, "hits.jsonl")))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------------- settings.json merge


PRE_EXISTING = json.dumps(
    {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /opt/other.py"}]}
            ]
        },
    },
    indent=2,
) + "\n"


def settings_file(home, project, scope):
    if scope == "global":
        return os.path.join(home, ".claude", "settings.json")
    return os.path.join(project, ".claude", "settings.json")


def eval_settings(scope, seed_existing):
    home = tempfile.mkdtemp(prefix="speakingwords-home-")
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    os.makedirs(os.path.join(home, ".claude"), exist_ok=True)
    label = "%s scope, %s settings" % (scope, "existing" if seed_existing else "no")
    try:
        target = settings_file(home, project, scope)
        if seed_existing:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(PRE_EXISTING)

        args = ["init", "--hook", "--agent", "claude", "--scope", scope, "--voice", "terse"]
        proc = run_cli(args, home, project)
        if proc.returncode != 0:
            check("A2", "%s: install succeeds" % label, False, proc.stderr.strip())
            return
        first = open(target, "r", encoding="utf-8").read()

        data = json.loads(first)
        entries = [
            entry
            for group in data.get("hooks", {}).get("Stop", [])
            for entry in group.get("hooks", [])
            if "speakingwords" in entry.get("command", "")
        ]
        check("P3", "%s: exactly one speakingwords Stop entry" % label, len(entries) == 1, first)
        if entries:
            command = entries[0]["command"]
            check("P3", "%s: entry is a command hook" % label, entries[0].get("type") == "command")
            # Since P8 the command is `sh <hook_guard.sh> <hook_stop.py>`: the
            # wrapper probes for python3 so a machine without it exits clean
            # instead of erroring on every reply (A24).
            parts = command.split(" ")
            check(
                "P3",
                "%s: command points at an existing hook_stop.py" % label,
                parts[0] == "sh"
                and len(parts) == 3
                and parts[1].endswith("hook_guard.sh")
                and parts[2].endswith("hook_stop.py")
                and all(os.path.isfile(p) for p in parts[1:]),
                command,
            )
            check("P3", "%s: command is findable by the speakingwords tag" % label,
                  "speakingwords" in command)
        if seed_existing:
            check(
                "P3",
                "%s: pre-existing settings survive" % label,
                data["permissions"]["allow"] == ["Bash(ls:*)"]
                and data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "python3 /opt/other.py",
            )
            check(
                "P3",
                "%s: key order preserved" % label,
                list(data.keys())[:2] == ["permissions", "hooks"],
                str(list(data.keys())),
            )

        # Idempotency: install -> install is a zero-diff operation.
        proc = run_cli(args, home, project)
        second = open(target, "r", encoding="utf-8").read()
        check("A2", "%s: install -> install byte identical" % label, first == second)
        check(
            "A2",
            "%s: reinstall does not duplicate the entry" % label,
            first.count('"speakingwords') + first.count("speakingwords/scripts") == second.count('"speakingwords') + second.count("speakingwords/scripts"),
        )

        # Uninstall restores the file exactly, or removes it cleanly.
        removed = uninstall(home, project, scope)
        check("P3", "%s: uninstall reports one entry removed" % label, removed == 1, "removed=%s" % removed)
        after = open(target, "r", encoding="utf-8").read()
        if seed_existing:
            check(
                "P3",
                "%s: uninstall restores the file byte-for-byte" % label,
                after == PRE_EXISTING,
                repr(after[:200]),
            )
        else:
            parsed = json.loads(after)
            check(
                "P3",
                "%s: uninstall leaves no empty hooks scaffolding" % label,
                "hooks" not in parsed or "Stop" not in parsed.get("hooks", {}),
                after,
            )
        check(
            "P3",
            "%s: uninstall is idempotent" % label,
            uninstall(home, project, scope) == 0,
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def uninstall(home, project, scope):
    """Call lib/hooks.js directly — `unhook` is phase 5, the wiring is phase 3."""
    script = (
        "const h=require(%s);"
        "const r=h.uninstallClaudeHook({scope:%s,cwd:process.cwd()});"
        "process.stdout.write(String(r.removed));"
        % (json.dumps(os.path.join(ROOT, "lib", "hooks.js")), json.dumps(scope))
    )
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=project,
        env=dict(os.environ, SPEAKINGWORDS_HOME=home),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return "error: %s" % proc.stderr.strip()
    return int(proc.stdout.strip() or 0)


# ------------------------------------------------------------- A6 re-check


def eval_exit_codes():
    """A6 end to end: lint.py only ever exits 0 or 2, and the hook only ever 0."""
    bad = []
    for kind in ("clean", "violations"):
        folder = os.path.join(FIXTURES, kind)
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            for voice in ("terse", "convo"):
                code = subprocess.run(
                    [sys.executable, LINT, "--voice", voice, path],
                    capture_output=True,
                    text=True,
                ).returncode
                if code not in (0, 2):
                    bad.append("%s/%s (%s) -> %s" % (kind, name, voice, code))
    check("A6", "lint.py exits only 0 or 2 across every fixture and voice", not bad, "; ".join(bad[:5]))


# -------------------------------------------------------------------- main


def main():
    eval_hook("terse")
    eval_settings("local", seed_existing=True)
    eval_settings("local", seed_existing=False)
    eval_settings("global", seed_existing=True)
    eval_exit_codes()

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 3 deterministic evals", ""]
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
    out.append("PHASE 3 PASS" if not failures else "PHASE 3 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
