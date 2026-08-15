#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 15 (mode: both).

No model calls, no network. SPEAKINGWORDS_HOME fakes the home directory, so
every install path runs inside a throwaway temp tree.

What is gated here
------------------
  A31  At `mode: both` the contract reaches the model exactly once. The memory
       block is present, and no SessionStart entry for speakingwords exists in
       any agent config. Both directions are checked, on both agents: hook mode
       keeps its injector, both mode has none — and switching a hook install to
       both takes the injector back out rather than leaving it behind.
  A32  `unhook` at both leaves a working memory install: block untouched, hook
       gone, `pref.json` says `memory`. A later `init --both` restores the mode,
       and running it twice is a zero-diff no-op.
  A25  A both-mode pref survives `status` and `update` with its unknown keys and
       its mode intact. Forward compatibility does not get an exemption for the
       new mode value.

  P15  Plumbing: `--both` is accepted as a flag and rejected values still route
       to help; `init --both` is idempotent byte for byte; `status` reports both
       layers and names the mode; `update` moves the block and the lexicon in
       one pass.

E12 (bounce rate at both vs hook-only) costs model calls and is recorded at
release, like E8 and E9. Nothing here calls a model.

Usage:  python3 evals/run_p15.py
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

MODERN_CODEX = "0.124.0"
START_MARKER = "<!-- speakingwords:start -->"
TAG = "speakingwords"

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


def read_json(path):
    try:
        return json.loads(read(path))
    except (OSError, ValueError):
        return None


def install(home, cwd, mode, agent="both", scope="global"):
    """One fully specified init, so nothing is ever waiting on a prompt."""
    return run_cli(
        ["init", "--%s" % mode, "--agent", agent, "--scope", scope,
         "--voice", "terse", "--conciseness", "high"],
        home, cwd,
    )


# --- the two things every A31 check asks about ---------------------------


def config_paths(home, cwd, scope="global"):
    """Where each agent's hook wiring lives, for the scope under test."""
    if scope == "global":
        return {
            "Claude Code": os.path.join(home, ".claude", "settings.json"),
            "Codex CLI": os.path.join(home, ".codex", "hooks.json"),
        }
    return {
        "Claude Code": os.path.join(cwd, ".claude", "settings.json"),
        "Codex CLI": os.path.join(cwd, ".codex", "hooks.json"),
    }


def memory_paths(home, cwd, scope="global"):
    if scope == "global":
        return {
            "Claude Code": os.path.join(home, ".claude", "CLAUDE.md"),
            "Codex CLI": os.path.join(home, ".codex", "AGENTS.md"),
        }
    return {
        "Claude Code": os.path.join(cwd, "CLAUDE.local.md"),
        "Codex CLI": os.path.join(cwd, "AGENTS.md"),
    }


def events(path, agent):
    """The lifecycle-event container of a config file, whatever agent owns it.

    Claude Code nests events under a "hooks" key; Codex puts them at the root of
    hooks.json. Reading both here keeps every A31 check agent-agnostic.
    """
    data = read_json(path)
    if not isinstance(data, dict):
        return {}
    if agent == "Claude Code":
        nested = data.get("hooks")
        return nested if isinstance(nested, dict) else {}
    return data


def has_entry(path, agent, event):
    """Is one of our entries wired for this event?

    Matched the way the installer marks its own work: a command string carrying
    the speakingwords tag. Same test the uninstaller and `status` use.
    """
    groups = events(path, agent).get(event)
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in group.get("hooks") or []:
            if isinstance(entry, dict) and TAG in str(entry.get("command", "")):
                return True
    return False


def has_block(path):
    try:
        return START_MARKER in read(path)
    except OSError:
        return False


def snapshot(root):
    """Every file under a tree, by content hash — the byte-identical test."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def diff_trees(before, after):
    """Human-readable list of what moved between two snapshots."""
    moved = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            moved.append(key)
    return moved


# ------------------------------------------------ A31: stated once, not twice


def eval_a31_both():
    """Both mode: block present on every target, injector nowhere."""
    home, proj = make_home(), make_project()
    try:
        proc = install(home, proj, "both")
        check("A31", "init --both exits 0", proc.returncode == 0, proc.stderr.strip())

        pref = read_json(os.path.join(claude_root(home), "pref.json")) or {}
        check("A31", "pref.json records mode both", pref.get("mode") == "both", json.dumps(pref))

        for agent, path in memory_paths(home, proj).items():
            check("A31", "[%s] memory block is written at both" % agent, has_block(path), path)

        for agent, path in config_paths(home, proj).items():
            check("A31", "[%s] Stop hook is wired at both" % agent,
                  has_entry(path, agent, "Stop"), path)
            # The whole point of the mode: the block already states the rules, so
            # a SessionStart entry here would state them a second time.
            check("A31", "[%s] no SessionStart entry at both" % agent,
                  not has_entry(path, agent, "SessionStart"), path)
            # Belt and braces: the tag must not appear under SessionStart at all,
            # even in a shape has_entry() would not recognise.
            raw = json.dumps(events(path, agent).get("SessionStart", []))
            check("A31", "[%s] SessionStart holds no speakingwords text" % agent,
                  TAG not in raw, raw)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_a31_hook():
    """The other direction: hook mode keeps the injector it always had."""
    home, proj = make_home(), make_project()
    try:
        proc = install(home, proj, "hook")
        check("A31", "init --hook exits 0", proc.returncode == 0, proc.stderr.strip())

        for agent, path in config_paths(home, proj).items():
            check("A31", "[%s] hook mode wires SessionStart" % agent,
                  has_entry(path, agent, "SessionStart"), path)
            check("A31", "[%s] hook mode wires Stop" % agent,
                  has_entry(path, agent, "Stop"), path)

        for agent, path in memory_paths(home, proj).items():
            check("A31", "[%s] hook mode writes no memory block" % agent,
                  not has_block(path), path)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_a31_switch():
    """A hook install moved to both must lose its injector, not keep it.

    This is the failure the assertion is really guarding: leaving the old
    SessionStart entry behind would put the contract in context twice on every
    install that ever ran hook mode first, and nothing else would complain.
    """
    home, proj = make_home(), make_project()
    try:
        install(home, proj, "hook")
        install(home, proj, "both")
        for agent, path in config_paths(home, proj).items():
            check("A31", "[%s] hook -> both removes the injector" % agent,
                  not has_entry(path, agent, "SessionStart"), read(path))
            check("A31", "[%s] hook -> both keeps the Stop hook" % agent,
                  has_entry(path, agent, "Stop"), path)
            check("A31", "[%s] hook -> both writes the block" % agent,
                  has_block(memory_paths(home, proj)[agent]), path)

        # And back again: both -> hook restores the injector, so the switch is
        # not a one-way door.
        install(home, proj, "hook")
        for agent, path in config_paths(home, proj).items():
            check("A31", "[%s] both -> hook restores the injector" % agent,
                  has_entry(path, agent, "SessionStart"), read(path))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_a31_local():
    """Scope changes where the files are, never whether the injector exists."""
    home, proj = make_home(), make_project()
    try:
        proc = install(home, proj, "both", scope="local")
        check("A31", "init --both --scope local exits 0", proc.returncode == 0, proc.stderr.strip())
        for agent, path in config_paths(home, proj, "local").items():
            check("A31", "[%s] no SessionStart entry at both, local scope" % agent,
                  not has_entry(path, agent, "SessionStart"), path)
        for agent, path in memory_paths(home, proj, "local").items():
            check("A31", "[%s] memory block is written at both, local scope" % agent,
                  has_block(path), path)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


# ------------------------------------------------- A32: unhook degrades to memory


def eval_a32_degrade():
    home, proj = make_home(), make_project()
    try:
        install(home, proj, "both")
        blocks_before = {a: read(p) for a, p in memory_paths(home, proj).items()}

        proc = run_cli(["unhook", "--yes"], home, proj)
        check("A32", "unhook at both exits 0", proc.returncode == 0, proc.stderr.strip())
        check("A32", "unhook says the install is now memory mode",
              "memory" in proc.stdout, proc.stdout)

        for agent, path in memory_paths(home, proj).items():
            check("A32", "[%s] block survives unhook byte for byte" % agent,
                  read(path) == blocks_before[agent], path)

        for agent, path in config_paths(home, proj).items():
            check("A32", "[%s] Stop hook is gone after unhook" % agent,
                  not has_entry(path, agent, "Stop"), path)

        pref = read_json(os.path.join(claude_root(home), "pref.json")) or {}
        check("A32", "pref.json mode becomes memory", pref.get("mode") == "memory",
              json.dumps(pref))
        # The rest of the install is untouched: unhook removes enforcement, not
        # the answers the user already gave.
        check("A32", "unhook keeps voice and conciseness",
              pref.get("voice") == "terse" and pref.get("conciseness") == "high",
              json.dumps(pref))

        # --- restore, and prove the restore is idempotent ---
        proc = install(home, proj, "both")
        check("A32", "init --both restores after unhook", proc.returncode == 0, proc.stderr.strip())
        pref = read_json(os.path.join(claude_root(home), "pref.json")) or {}
        check("A32", "restored pref.json says both", pref.get("mode") == "both", json.dumps(pref))
        for agent, path in config_paths(home, proj).items():
            check("A32", "[%s] Stop hook is back" % agent, has_entry(path, agent, "Stop"), path)
            check("A32", "[%s] restore wires no injector" % agent,
                  not has_entry(path, agent, "SessionStart"), path)

        before = snapshot(home)
        install(home, proj, "both")
        moved = diff_trees(before, snapshot(home))
        check("A32", "a second restore is a zero-diff no-op", not moved, ", ".join(moved))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


# ------------------------------------------------------------- P15 plumbing


def eval_p15_idempotent():
    """Two both-mode installs into one home leave an identical tree (A2/A12)."""
    home, proj = make_home(), make_project()
    try:
        install(home, proj, "both")
        before = snapshot(home)
        proc = install(home, proj, "both")
        moved = diff_trees(before, snapshot(home))
        check("P15", "second init --both exits 0", proc.returncode == 0, proc.stderr.strip())
        check("P15", "init --both is byte-identical on a second run", not moved, ", ".join(moved))

        # A hand-added neighbour must survive the second install, same as in
        # every other mode: only our own entries are ours to rewrite.
        settings_path = config_paths(home, proj)["Claude Code"]
        data = read_json(settings_path)
        data.setdefault("env", {})["MINE"] = "1"
        with open(settings_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        install(home, proj, "both")
        after = read_json(settings_path) or {}
        check("P15", "a foreign settings key survives init --both",
              (after.get("env") or {}).get("MINE") == "1", json.dumps(after))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_p15_flag():
    """`--both` is a real mode value, and a wrong one still routes to help."""
    home, proj = make_home(), make_project()
    try:
        proc = run_cli(
            ["init", "--mode", "both", "--agent", "claude", "--scope", "global",
             "--voice", "terse", "--conciseness", "high"],
            home, proj,
        )
        check("P15", "--mode both is accepted", proc.returncode == 0, proc.stderr.strip())

        proc = run_cli(
            ["init", "--mode", "neither", "--agent", "claude", "--scope", "global",
             "--voice", "terse"],
            home, proj,
        )
        check("P15", "a bad --mode exits 1", proc.returncode == 1, proc.stdout)
        check("P15", "a bad --mode names the three modes it takes",
              all(word in proc.stderr for word in ("memory", "hook", "both")), proc.stderr)
        check("P15", "a bad --mode writes nothing to stdout", proc.stdout == "", proc.stdout)

        # The overview has to list the flag, or a user reading help cannot find
        # the mode at all (A15).
        proc = run_cli(["help", "init"], home, proj)
        check("P15", "help init lists --both", "--both" in proc.stdout, proc.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_p15_status():
    """`status` at both names the mode and reports each layer separately."""
    home, proj = make_home(), make_project()
    try:
        install(home, proj, "both")
        proc = run_cli(["status"], home, proj)
        check("P15", "status at both exits 0", proc.returncode == 0, proc.stderr.strip())
        check("P15", "status names both mode", "both mode" in proc.stdout, proc.stdout)
        check("P15", "status reports the memory block layer",
              "memory block" in proc.stdout and "present" in proc.stdout, proc.stdout)
        check("P15", "status reports the hook layer",
              "Stop hook" in proc.stdout and "wired" in proc.stdout, proc.stdout)
        check("P15", "status reports the absent injector as intended",
              "none (by design)" in proc.stdout, proc.stdout)
        check("P15", "status still reports the linter counters",
              "No hits recorded yet" in proc.stdout, proc.stdout)

        # A missing half must read as missing, not as silence. Take the block
        # out by hand and status has to say so.
        target = memory_paths(home, proj)["Claude Code"]
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("nothing of ours here\n")
        proc = run_cli(["status"], home, proj)
        check("P15", "status flags a block that has gone missing",
              "ABSENT" in proc.stdout, proc.stdout)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


def eval_p15_update():
    """`update` at both moves the lexicon and the block in the same pass."""
    home, proj = make_home(), make_project()
    try:
        install(home, proj, "both")
        proc = run_cli(["update", "less flimflam"], home, proj)
        check("P15", "update at both exits 0", proc.returncode == 0, proc.stderr.strip())

        lexicon = os.path.join(claude_root(home), "refs", "lexicon.md")
        check("P15", "update edits the installed lexicon",
              "strip-user-flimflam" in read(lexicon), proc.stdout)
        check("P15", "update re-renders the block",
              "memory block re-rendered" in proc.stdout, proc.stdout)

        # Same pass, both artefacts: the block's banned-word line is derived from
        # the lexicon, so the new rule has to show up inside the markers too.
        for agent, path in memory_paths(home, proj).items():
            check("P15", "[%s] the re-rendered block carries the new rule" % agent,
                  "flimflam" in read(path), path)

        # A4 discipline holds at both: every touched file was backed up first.
        check("P15", "update backs up the lexicon", os.path.exists(lexicon + ".bak"), lexicon)
        for agent, path in memory_paths(home, proj).items():
            check("P15", "[%s] update backs up the memory file" % agent,
                  os.path.exists(path + ".bak"), path)

        pref = read_json(os.path.join(claude_root(home), "pref.json")) or {}
        check("P15", "update leaves the mode alone", pref.get("mode") == "both", json.dumps(pref))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


# ------------------------------------------- A25: the pref survives every util


def eval_a25_roundtrip():
    """A both pref, with a key this version never heard of, comes back whole."""
    home, proj = make_home(), make_project()
    try:
        install(home, proj, "both")
        pref_path = os.path.join(claude_root(home), "pref.json")
        record = read_json(pref_path)
        record["from_a_later_version"] = {"register": "ste"}
        with open(pref_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record, indent=2) + "\n")

        run_cli(["status"], home, proj)
        run_cli(["update", "less flimflam"], home, proj)

        after = read_json(pref_path) or {}
        check("A25", "the unknown key survives status and update",
              after.get("from_a_later_version") == {"register": "ste"}, json.dumps(after))
        check("A25", "the mode stays both across both utils",
              after.get("mode") == "both", json.dumps(after))
        check("A25", "voice and conciseness survive too",
              after.get("voice") == "terse" and after.get("conciseness") == "high",
              json.dumps(after))

        # And through the degrade, which is the one util that rewrites the mode
        # on purpose.
        run_cli(["unhook", "--yes"], home, proj)
        after = read_json(pref_path) or {}
        check("A25", "the unknown key survives unhook",
              after.get("from_a_later_version") == {"register": "ste"}, json.dumps(after))
        check("A25", "unhook writes memory, not unhooked",
              after.get("mode") == "memory", json.dumps(after))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(proj, ignore_errors=True)


# -------------------------------------------------------------------- main


def main():
    eval_a31_both()
    eval_a31_hook()
    eval_a31_switch()
    eval_a31_local()
    eval_a32_degrade()
    eval_p15_idempotent()
    eval_p15_flag()
    eval_p15_status()
    eval_p15_update()
    eval_a25_roundtrip()

    notes.append("E12 (bounce rate at both vs hook-only) needs model calls and is recorded at release.")

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 15 deterministic evals", ""]
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
    out.append("PHASE 15 PASS" if not failures else "PHASE 15 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
