#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 4 (Codex adapter).

No model calls, no network, no real Codex binary, nothing outside a throwaway
temp tree. SPEAKINGWORDS_HOME fakes the home directory, a temp cwd fakes the
project, and SPEAKINGWORDS_CODEX_VERSION fakes the installed Codex — so the
degraded path is testable on a machine that has never had Codex on it.

What is gated here
------------------
  E7   Cross-agent parity. The same reply fed to the Claude Code hook and to
       the Codex hook produces the same verdict and the same hits.jsonl rule
       ids. The payload adapter introduces zero behavioural difference.
  A11  A both-agents install ships ONE core. Every file under the shared skill
       root is byte-identical to the repo copy, and the Codex wiring points at
       that shared root rather than at a second copy.
  A12  Codex config edits are idempotent and preserve user content. hooks.json
       and config.toml both survive install -> install with zero diff, and
       install -> uninstall byte-for-byte.
  A13  On Codex below v0.124.0 the installer never writes a hooks.json entry.
       It wires the notify audit fallback instead and says so plainly.

Plus the supporting behaviour those rest on: version detection, the trust-step
notice, notify's observe-never-block contract, and fail-open on every
payload shape the Codex hook might be handed.

Usage:  python3 evals/run_p4.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLI = os.path.join(ROOT, "bin", "speakingwords.js")
HOOKS_LIB = os.path.join(ROOT, "lib", "hooks.js")
REPO_SKILL = os.path.join(ROOT, "skill")
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "manifest.json")

# Any version at or above the stable hooks engine, and any below it. Pinned as
# strings so the eval never depends on a real `codex --version`.
MODERN_CODEX = "0.124.0"
OLD_CODEX = "0.120.0"

VIOLATING_REPLY = (
    "Landed the retry fix on the worker queue.\n"
    "\n"
    "You're absolutely right that the backoff was too aggressive. I hope this helps!\n"
)

results = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def run_cli(args, home, cwd, codex_version=MODERN_CODEX):
    env = dict(os.environ, SPEAKINGWORDS_HOME=home)
    if codex_version is None:
        env.pop("SPEAKINGWORDS_CODEX_VERSION", None)
    else:
        env["SPEAKINGWORDS_CODEX_VERSION"] = codex_version
    return subprocess.run(["node", CLI] + args, cwd=cwd, env=env, capture_output=True, text=True)


def run_node(script, home, cwd):
    """Call into lib/hooks.js directly, for wiring that has no CLI verb yet."""
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


def claude_root(home):
    return os.path.join(home, ".claude", "skills", "speakingwords")


def codex_root(home):
    return os.path.join(home, ".codex", "speakingwords")


def run_script(skill_root, name, stdin=None, argv=None):
    return subprocess.run(
        [sys.executable, os.path.join(skill_root, "scripts", name)] + (argv or []),
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
    )


def write_transcript(path, reply_text):
    lines = [
        {"type": "user", "message": {"role": "user", "content": "why is the queue retrying?"}},
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": reply_text}]},
        },
    ]
    with open(path, "w", encoding="utf-8") as fh:
        for entry in lines:
            fh.write(json.dumps(entry) + "\n")


def hits_records(skill_root):
    path = os.path.join(skill_root, "hits.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh.read().split("\n"):
            if line.strip():
                out.append(json.loads(line))
    return out


def rule_ids(skill_root):
    return [r.get("rule") for r in hits_records(skill_root)]


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tree_files(root):
    """Every file under root, as paths relative to it, sorted."""
    out = []
    for base, _dirs, names in os.walk(root):
        for name in names:
            out.append(os.path.relpath(os.path.join(base, name), root))
    return sorted(out)


# ------------------------------------------------------- E7 cross-agent parity


def sample_fixtures(per_class=5):
    """A deterministic spread across the fixture set, not just the first few."""
    manifest = json.load(open(MANIFEST, "r", encoding="utf-8"))
    picked = []
    for kind in ("violations", "clean"):
        entry = manifest[kind]
        # violations maps name -> expected rule ids; clean is a plain list.
        names = sorted(entry) if isinstance(entry, dict) else list(entry)
        stride = max(1, len(names) // per_class)
        picked.extend((kind, name) for name in names[::stride][:per_class])
    return picked


def normalise(reason, skill_root):
    """Strip the one thing that legitimately differs: the install root path.

    A Claude-only and a Codex-only install put the core in different places, so
    the SKILL.md path inside the feedback differs by design. Everything else in
    the reason — the rule ids, the matched text, the voice — must match.
    """
    return reason.replace(skill_root, "<ROOT>")


def verdict(proc, skill_root):
    if proc.stdout.strip() == "":
        return ("approve", "")
    payload = json.loads(proc.stdout)
    return (payload.get("decision"), normalise(payload.get("reason", ""), skill_root))


def eval_parity(voice="terse"):
    """Same text, three entry paths, one verdict.

    Three separate installs so the hits.jsonl files never mix: Claude Code,
    Codex fed a mirrored transcript payload, and Codex fed only the trailing
    assistant message (the degraded payload shape).
    """
    homes = {"claude": make_home(), "codex": make_home(), "codex_msg": make_home()}
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        ok = True
        for key, agent in (("claude", "claude"), ("codex", "codex"), ("codex_msg", "codex")):
            proc = run_cli(
                ["init", "--hook", "--agent", agent, "--scope", "local", "--voice", voice],
                homes[key],
                project,
            )
            if proc.returncode != 0:
                check("E7", "%s install succeeds" % key, False, proc.stderr.strip())
                ok = False
        if not ok:
            return

        roots = {
            "claude": claude_root(homes["claude"]),
            "codex": codex_root(homes["codex"]),
            "codex_msg": codex_root(homes["codex_msg"]),
        }
        check(
            "E7",
            "Codex install ships hook_codex.py and notify_codex.py",
            os.path.isfile(os.path.join(roots["codex"], "scripts", "hook_codex.py"))
            and os.path.isfile(os.path.join(roots["codex"], "scripts", "notify_codex.py")),
        )

        transcript = os.path.join(project, "transcript.jsonl")
        mismatched = []
        blocked = 0
        approved = 0

        for kind, name in sample_fixtures():
            text = open(os.path.join(FIXTURES, kind, name), "r", encoding="utf-8").read()
            write_transcript(transcript, text)
            payload = {
                "hook_event_name": "Stop",
                "transcript_path": transcript,
                "stop_hook_active": False,
            }

            a = verdict(
                run_script(roots["claude"], "hook_stop.py", json.dumps(payload)),
                roots["claude"],
            )
            b = verdict(
                run_script(roots["codex"], "hook_codex.py", json.dumps(payload)),
                roots["codex"],
            )
            # Degraded shape: no transcript key at all, only the reply text.
            c = verdict(
                run_script(
                    roots["codex_msg"],
                    "hook_codex.py",
                    json.dumps({"hook_event_name": "Stop", "last-assistant-message": text}),
                ),
                roots["codex_msg"],
            )

            if a != b:
                mismatched.append("%s (transcript): %r vs %r" % (name, a[0], b[0]))
            if a != c:
                mismatched.append("%s (message key): %r vs %r" % (name, a[0], c[0]))
            if a[0] == "block":
                blocked += 1
            else:
                approved += 1

        check("E7", "every sampled fixture reaches an identical verdict on both agents",
              not mismatched, "; ".join(mismatched[:4]))
        check("E7", "the sample exercises both outcomes",
              blocked > 0 and approved > 0, "blocked=%d approved=%d" % (blocked, approved))

        claude_rules = rule_ids(roots["claude"])
        check("E7", "Codex logs the same hits.jsonl rule ids, in the same order",
              rule_ids(roots["codex"]) == claude_rules,
              "%s vs %s" % (rule_ids(roots["codex"])[:6], claude_rules[:6]))
        check("E7", "the degraded payload shape logs the same rule ids too",
              rule_ids(roots["codex_msg"]) == claude_rules)
        check("E7", "the parity run actually logged something", len(claude_rules) > 0)
        check(
            "E7",
            "hits records differ only by timestamp",
            [{k: v for k, v in r.items() if k != "ts"} for r in hits_records(roots["codex"])]
            == [{k: v for k, v in r.items() if k != "ts"} for r in hits_records(roots["claude"])],
        )

        # --- loop guard and fail-open on the Codex entry point ---
        write_transcript(transcript, VIOLATING_REPLY)
        guarded = run_script(
            roots["codex"],
            "hook_codex.py",
            json.dumps({"transcript_path": transcript, "stop_hook_active": True}),
        )
        check("A5", "Codex hook: guard up means no block",
              guarded.returncode == 0 and guarded.stdout.strip() == "", guarded.stdout[:160])

        for label, stdin in (
            ("malformed stdin", "{not json"),
            ("empty stdin", ""),
            ("payload is not an object", "[1,2,3]"),
            ("no usable keys", "{}"),
            ("missing transcript", json.dumps({"transcript_path": "/nope/none.jsonl"})),
            ("null message", json.dumps({"last-assistant-message": None})),
        ):
            proc = run_script(roots["codex"], "hook_codex.py", stdin)
            check("E7", "Codex hook fails open on %s" % label,
                  proc.returncode == 0 and proc.stdout.strip() == "",
                  "exit %s out %r" % (proc.returncode, proc.stdout[:120]))
    finally:
        for home in homes.values():
            shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------------- A11 one shared core


def eval_shared_core():
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        proc = run_cli(
            ["init", "--hook", "--agent", "both", "--scope", "local", "--voice", "terse"],
            home,
            project,
        )
        if proc.returncode != 0:
            check("A11", "both-agents install succeeds", False, proc.stderr.strip())
            return
        summary = proc.stdout
        shared = claude_root(home)

        # Every shipped file, byte for byte against the repo copy.
        repo_files = tree_files(REPO_SKILL)
        check("A11", "the repo ships the files the plan lists",
              {"SKILL.md", os.path.join("refs", "lexicon.md"),
               os.path.join("scripts", "lint.py")}.issubset(set(repo_files)),
              str(repo_files))
        differing = []
        for rel in repo_files:
            installed = os.path.join(shared, rel)
            if not os.path.isfile(installed):
                differing.append("%s missing" % rel)
            elif sha256(installed) != sha256(os.path.join(REPO_SKILL, rel)):
                differing.append("%s checksum" % rel)
        check("A11", "every core file under the shared root matches the repo checksum",
              not differing, "; ".join(differing[:5]))

        check("A11", "the shared core is the Claude Code root (plan §7)",
              os.path.isfile(os.path.join(shared, "SKILL.md")))
        check("A11", "no second core was written under the Codex root",
              not os.path.exists(os.path.join(codex_root(home), "SKILL.md")),
              codex_root(home))

        # The wiring is what proves the sharing: both commands must resolve into
        # the same directory, or `update` would only ever fix one agent.
        settings = json.load(open(os.path.join(project, ".claude", "settings.json"), "r", encoding="utf-8"))
        claude_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        hooks_json = json.load(open(os.path.join(project, ".codex", "hooks.json"), "r", encoding="utf-8"))
        codex_cmd = hooks_json["Stop"][0]["hooks"][0]["command"]

        check("A11", "the Codex hook command points at the shared root",
              shared in codex_cmd, codex_cmd)
        check("A11", "the Claude hook command points at the same shared root",
              shared in claude_cmd, claude_cmd)
        check("A11", "each agent gets its own entry script under that one root",
              claude_cmd.endswith("hook_stop.py") and codex_cmd.endswith("hook_codex.py"),
              "%s | %s" % (claude_cmd, codex_cmd))
        check("A11", "both hook commands exist on disk",
              os.path.isfile(claude_cmd.split(" ", 1)[1]) and os.path.isfile(codex_cmd.split(" ", 1)[1]))

        pref = json.load(open(os.path.join(shared, "pref.json"), "r", encoding="utf-8"))
        check("A11", "pref.json records both agents", pref.get("agents") == ["claude", "codex"], json.dumps(pref))
        check("A11", "pref.json records hook mode", pref.get("mode") == "hook")
        check("A11", "only one pref.json exists", not os.path.exists(os.path.join(codex_root(home), "pref.json")))

        # Codex events sit at the ROOT of hooks.json, not under a "hooks" key.
        check("A12", "hooks.json puts Stop at the root, with no wrapping hooks key",
              "Stop" in hooks_json and "hooks" not in hooks_json, json.dumps(hooks_json)[:200])

        check("A11", "summary names both agents", "Claude Code" in summary and "Codex" in summary)
        check("A11", "summary states the shared core", "share one core" in summary, summary[-600:])
        check("P4", "summary surfaces the Codex trust step",
              "trust" in summary.lower() and "/hooks" in summary, summary[-800:])
        check("P4", "summary prints the wiring path for each agent",
              os.path.join(project, ".claude", "settings.json") in summary
              and os.path.join(project, ".codex", "hooks.json") in summary)
        check("A13", "a modern Codex gets no downgrade note",
              "DOWNGRADED" not in summary and "audit-only" not in summary)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# -------------------------------------------------------- A12 hooks.json edits


PRE_EXISTING_HOOKS = json.dumps(
    {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /opt/other.py"}]}
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "python3 /opt/mine.py"}]}],
    },
    indent=2,
) + "\n"


def eval_hooks_json(scope, seed_existing):
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    label = "%s scope, %s hooks.json" % (scope, "existing" if seed_existing else "no")
    try:
        target = (
            os.path.join(home, ".codex", "hooks.json")
            if scope == "global"
            else os.path.join(project, ".codex", "hooks.json")
        )
        if seed_existing:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(PRE_EXISTING_HOOKS)

        args = ["init", "--hook", "--agent", "codex", "--scope", scope, "--voice", "terse"]
        proc = run_cli(args, home, project)
        if proc.returncode != 0:
            check("A12", "%s: install succeeds" % label, False, proc.stderr.strip())
            return
        first = open(target, "r", encoding="utf-8").read()
        data = json.loads(first)

        entries = [
            entry
            for group in data.get("Stop", [])
            for entry in group.get("hooks", [])
            if "speakingwords" in entry.get("command", "")
        ]
        check("A12", "%s: exactly one speakingwords Stop entry" % label, len(entries) == 1, first)
        if entries:
            command = entries[0]["command"]
            check("A12", "%s: entry is a command hook" % label, entries[0].get("type") == "command")
            check("A12", "%s: command points at an existing hook_codex.py" % label,
                  command.startswith("python3 ") and os.path.isfile(command.split(" ", 1)[1]), command)

        if seed_existing:
            check("A12", "%s: the user's other event survives" % label,
                  data["PreToolUse"][0]["hooks"][0]["command"] == "python3 /opt/other.py")
            check("A12", "%s: the user's own Stop hook survives" % label,
                  any(e.get("command") == "python3 /opt/mine.py"
                      for g in data["Stop"] for e in g.get("hooks", [])), first)
            check("A12", "%s: key order preserved" % label,
                  list(data.keys())[:2] == ["PreToolUse", "Stop"], str(list(data.keys())))

        # Idempotency: install -> install is a zero-diff operation.
        run_cli(args, home, project)
        second = open(target, "r", encoding="utf-8").read()
        check("A12", "%s: install -> install byte identical" % label, first == second)

        # Uninstall restores the file exactly, or removes it cleanly.
        removed = run_node(
            "const r=h.uninstallCodexHook({scope:%s,cwd:process.cwd()});"
            "process.stdout.write(JSON.stringify(r));" % json.dumps(scope),
            home,
            project,
        )
        check("A12", "%s: uninstall reports one hooks.json entry removed" % label,
              removed.get("hooks", {}).get("removed") == 1, json.dumps(removed))
        if seed_existing:
            after = open(target, "r", encoding="utf-8").read()
            check("A12", "%s: uninstall restores hooks.json byte-for-byte" % label,
                  after == PRE_EXISTING_HOOKS, repr(after[:240]))
        else:
            check("A12", "%s: a hooks.json holding only our entry is removed" % label,
                  not os.path.exists(target), removed.get("hooks", {}).get("action"))

        again = run_node(
            "const r=h.uninstallCodexHook({scope:%s,cwd:process.cwd()});"
            "process.stdout.write(JSON.stringify(r));" % json.dumps(scope),
            home,
            project,
        )
        check("A12", "%s: uninstall is idempotent" % label, again.get("removed") == 0, json.dumps(again))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------- A13 degraded path and config.toml


PRE_EXISTING_TOML = (
    "# my codex config\n"
    "model = \"o3\"\n"
    "approval_policy = \"on-request\"\n"
    "\n"
    "[tui]\n"
    "theme = \"dark\"\n"
)


def eval_downgrade():
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        config = os.path.join(home, ".codex", "config.toml")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(PRE_EXISTING_TOML)

        args = ["init", "--hook", "--agent", "codex", "--scope", "local", "--voice", "terse"]
        proc = run_cli(args, home, project, codex_version=OLD_CODEX)
        if proc.returncode != 0:
            check("A13", "install on an old Codex succeeds", False, proc.stderr.strip())
            return
        summary = proc.stdout

        # A13's hard rule: no hooks.json entry, anywhere, on an old Codex.
        check("A13", "no project hooks.json is written",
              not os.path.exists(os.path.join(project, ".codex", "hooks.json")))
        check("A13", "no global hooks.json is written",
              not os.path.exists(os.path.join(home, ".codex", "hooks.json")))

        first = open(config, "r", encoding="utf-8").read()
        lines = first.split("\n")
        notify = [l for l in lines if l.strip().startswith("notify")]
        check("A13", "exactly one notify line is written", len(notify) == 1, first)
        if notify:
            check("A13", "notify points at notify_codex.py", "notify_codex.py" in notify[0], notify[0])
            value = json.loads(notify[0].split("=", 1)[1].strip())
            check("A13", "notify value is a program plus its script path",
                  isinstance(value, list) and value[0] == "python3" and os.path.isfile(value[1]),
                  notify[0])

        # Surgical edit: everything the user wrote is still there, untouched,
        # and our line landed in the top-level region rather than inside [tui].
        for original in PRE_EXISTING_TOML.split("\n"):
            if original.strip() == "":
                continue
            check("A12", "config.toml keeps the user's line %r" % original.strip()[:28],
                  original in lines, first)
        check("A12", "config.toml keeps the user's line order",
              lines.index("model = \"o3\"") < lines.index("[tui]")
              and lines.index("[tui]") < lines.index("theme = \"dark\""), first)
        check("A12", "the notify line sits in the top-level region, not inside [tui]",
              lines.index(notify[0]) < lines.index("[tui]"), first)

        # A12: install -> install is a zero-diff operation here too.
        run_cli(args, home, project, codex_version=OLD_CODEX)
        second = open(config, "r", encoding="utf-8").read()
        check("A12", "config.toml: install -> install byte identical", first == second)

        # A13: the downgrade must be stated plainly, not buried.
        check("A13", "summary states the downgrade", "DOWNGRADED" in summary, summary[-900:])
        check("A13", "summary names the version and the threshold",
              OLD_CODEX in summary and "0.124.0" in summary, summary[-900:])
        check("A13", "summary says enforcement is impossible, not merely degraded",
              "nothing can be blocked" in summary, summary[-900:])
        check("A13", "summary tells the user how to get enforcement back",
              "Upgrade Codex" in summary, summary[-900:])
        check("A13", "summary says notify is user-level despite the local scope",
              "user-level only" in summary, summary[-900:])
        check("A13", "no trust step is claimed when no hook was wired",
              "ONE STEP LEFT" not in summary)
        # An audit-only install must not describe a bounce it cannot perform.
        check("A13", "summary does not promise a bounce it cannot deliver",
              "bounced once" not in summary, summary[-900:])
        check("A13", "summary says what the audit pass can do instead",
              "can only tell you what hook mode would have caught" in summary, summary[-900:])
        check("A13", "the user-level note does not mention an uninstalled Claude Code",
              "applies to Claude Code" not in summary, summary[-900:])

        # Uninstall takes the notify line and leaves the rest byte-for-byte.
        removed = run_node(
            "const r=h.uninstallCodexHook({scope:'local',cwd:process.cwd()});"
            "process.stdout.write(JSON.stringify(r));",
            home,
            project,
        )
        check("A13", "uninstall removes the notify line", removed.get("notify", {}).get("removed") == 1,
              json.dumps(removed))
        check("A12", "uninstall restores config.toml byte-for-byte",
              open(config, "r", encoding="utf-8").read() == PRE_EXISTING_TOML,
              repr(open(config, "r", encoding="utf-8").read()[:240]))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_mixed_downgrade():
    """Both agents, old Codex: Claude Code still gets real enforcement.

    The degraded path is per-agent. A Codex too old for hooks must not drag
    Claude Code down with it — one shared core, one enforced wiring, one
    audit-only wiring.
    """
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        proc = run_cli(
            ["init", "--hook", "--agent", "both", "--scope", "local", "--voice", "terse"],
            home,
            project,
            codex_version=OLD_CODEX,
        )
        if proc.returncode != 0:
            check("A13", "mixed install succeeds", False, proc.stderr.strip())
            return
        summary = proc.stdout

        check("A13", "mixed: Claude Code still gets a real Stop hook",
              os.path.isfile(os.path.join(project, ".claude", "settings.json")))
        check("A13", "mixed: no Codex hooks.json on an old Codex",
              not os.path.exists(os.path.join(project, ".codex", "hooks.json")))
        check("A13", "mixed: Codex gets the notify fallback",
              os.path.isfile(os.path.join(home, ".codex", "config.toml")))
        check("A11", "mixed: one shared core at the Claude Code root",
              os.path.isfile(os.path.join(claude_root(home), "SKILL.md"))
              and not os.path.exists(os.path.join(codex_root(home), "SKILL.md")))

        notify = [l for l in open(os.path.join(home, ".codex", "config.toml"),
                                  "r", encoding="utf-8").read().split("\n")
                  if l.strip().startswith("notify")]
        check("A11", "mixed: the notify script is the shared core's copy",
              len(notify) == 1 and claude_root(home) in notify[0], str(notify))
        check("A13", "mixed: the summary marks only Codex as audit-only",
              "DOWNGRADED on Codex" in summary and "audit-only" in summary, summary[-900:])
        check("A13", "mixed: no trust step is claimed, since no Codex hook was wired",
              "ONE STEP LEFT" not in summary)
        check("A13", "mixed: the bounce promise is scoped to the agent that can keep it",
              "finishes on Claude Code" in summary, summary[-1200:])
        check("A13", "mixed: the local scope still applies to Claude Code, and says so",
              "still applies to Claude Code" in summary, summary[-1200:])
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_notify_conflict():
    """A foreign notify program is refused, never clobbered."""
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        config = os.path.join(home, ".codex", "config.toml")
        foreign = "notify = [\"python3\", \"/opt/my-own-notifier.py\"]\n"
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(foreign)

        proc = run_cli(
            ["init", "--hook", "--agent", "codex", "--scope", "global", "--voice", "terse"],
            home,
            project,
            codex_version=OLD_CODEX,
        )
        check("A12", "a foreign notify program makes install refuse", proc.returncode != 0, proc.stdout[-300:])
        check("A12", "the refusal quotes the line it will not touch",
              "/opt/my-own-notifier.py" in proc.stderr, proc.stderr[:300])
        check("A12", "the refusal says why, and what to do instead",
              "only one notify" in proc.stderr and "notify_codex.py" in proc.stderr, proc.stderr[:400])
        check("A12", "the foreign notify line is left byte-for-byte",
              open(config, "r", encoding="utf-8").read() == foreign)
        check("A12", "nothing was half-installed before the refusal",
              not os.path.exists(os.path.join(home, ".codex", "hooks.json"))
              and not os.path.exists(os.path.join(codex_root(home), "SKILL.md")))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


def eval_notify_behaviour():
    """The audit script observes and logs, and never does anything else."""
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        proc = run_cli(
            ["init", "--hook", "--agent", "codex", "--scope", "global", "--voice", "terse"],
            home,
            project,
            codex_version=OLD_CODEX,
        )
        if proc.returncode != 0:
            check("A13", "audit install succeeds", False, proc.stderr.strip())
            return
        root = codex_root(home)

        event = json.dumps({
            "type": "agent-turn-complete",
            "turn-id": "turn-7",
            "input-messages": ["why is the queue retrying?"],
            "last-assistant-message": VIOLATING_REPLY,
        })
        run = run_script(root, "notify_codex.py", argv=[event])
        check("A13", "notify exits 0", run.returncode == 0, run.stderr.strip())
        check("A13", "notify never emits a decision — it cannot block", run.stdout.strip() == "",
              run.stdout[:200])

        records = hits_records(root)
        check("A13", "notify logs the violations it saw", len(records) > 0)
        check("A13", "every audit record is marked as audit-only",
              records and all(r.get("audit") is True and r.get("agent") == "codex" for r in records),
              json.dumps(records[:2]))
        check("A13", "audit records carry the turn id", records and all(r.get("turn") == "turn-7" for r in records))
        check("A9", "every audit record is valid single-line JSON with ts/rule/match",
              records and all(isinstance(r.get("ts"), str) and r["ts"].endswith("Z")
                              and isinstance(r.get("rule"), str) and "match" in r for r in records),
              json.dumps(records[:1]))

        before = len(records)
        for label, argv in (
            ("no argument", []),
            ("a different event type", [json.dumps({"type": "agent-turn-started"})]),
            ("malformed JSON", ["{not json"]),
            ("a clean reply", [json.dumps({"type": "agent-turn-complete",
                                           "last-assistant-message": "- Retry count is now three.\n"})]),
        ):
            run = run_script(root, "notify_codex.py", argv=argv)
            check("A13", "notify stays silent and exits 0 on %s" % label,
                  run.returncode == 0 and run.stdout.strip() == "", run.stdout[:120])
        check("A13", "notify logs nothing it should not", len(hits_records(root)) == before)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# ------------------------------------------------------ version detection gate


def eval_version_detection():
    home = make_home()
    project = tempfile.mkdtemp(prefix="speakingwords-project-")
    try:
        cases = [
            ("0.124.0", True, "the exact stable version supports hooks"),
            ("0.125.3", True, "a newer version supports hooks"),
            ("1.0.0", True, "a major bump supports hooks"),
            ("0.123.9", False, "one patch below the threshold does not"),
            ("0.120.0", False, "an older version does not"),
            ("0.99.99", False, "a lower minor does not"),
        ]
        for version, expected, why in cases:
            got = run_node(
                "process.stdout.write(JSON.stringify(h.codexSupportsHooks(%s)));" % json.dumps(version),
                home,
                project,
            )
            check("P4", "version gate: %s" % why, got is expected, "%s -> %s" % (version, got))

        # Unknown is not old. Treating it as old would silently downgrade a
        # perfectly capable Codex to audit-only.
        got = run_node("process.stdout.write(JSON.stringify(h.codexSupportsHooks(null)));", home, project)
        check("P4", "an unknown version is treated as capable, not as old", got is True, str(got))

        env_read = subprocess.run(
            ["node", "-e",
             "const h=require(%s);process.stdout.write(JSON.stringify(h.codexCapability()));"
             % json.dumps(HOOKS_LIB)],
            cwd=project,
            env=dict(os.environ, SPEAKINGWORDS_HOME=home, SPEAKINGWORDS_CODEX_VERSION="v0.121.4"),
            capture_output=True,
            text=True,
        )
        capability = json.loads(env_read.stdout)
        check("P4", "SPEAKINGWORDS_CODEX_VERSION overrides the binary lookup",
              capability["version"] == "v0.121.4" and capability["supportsHooks"] is False,
              json.dumps(capability))
        check("P4", "a known-old version is reported as downgraded",
              capability["downgraded"] is True, json.dumps(capability))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(project, ignore_errors=True)


# -------------------------------------------------------------------- main


def main():
    eval_parity("terse")
    eval_shared_core()
    eval_hooks_json("local", seed_existing=True)
    eval_hooks_json("local", seed_existing=False)
    eval_hooks_json("global", seed_existing=True)
    eval_downgrade()
    eval_mixed_downgrade()
    eval_notify_conflict()
    eval_notify_behaviour()
    eval_version_detection()

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 4 deterministic evals", ""]
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
    out.append("PHASE 4 PASS" if not failures else "PHASE 4 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
