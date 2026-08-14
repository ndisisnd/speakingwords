#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 6 (packaging and distribution).

Fully offline. No network, no npm registry, no model calls. The "download" the
installer performs is a file:// URL pointing at a tarball this script just built
with `npm pack`, and every install lands in a throwaway HOME.

What is gated here
------------------
  A8   The cURL path and the npm path produce identical skill/, bin/ and lib/
       trees. Checked file-by-file by SHA-256 across three trees: the repo, the
       npm-extracted tarball, and the install.sh-installed directory.

Plus the packaging contract that A8 rests on:

  Tarball    the `files` allowlist ships bin/, lib/, skill/, README.md and
             LICENSE — and nothing else. No plan/, no evals/, no install.sh,
             no .bak, no hits.jsonl, no pref.json.
  Checksum   a mismatched SHA-256 refuses and installs nothing; a missing
             checksum refuses unless --insecure is passed explicitly.
  Node gate  Node < 18 refuses with a plain message and installs nothing.
  Symlink    the CLI is linked into ~/.local/bin and answers `version` with the
             package.json version.
  Uninstall  --uninstall removes the symlink and the app tree.

Usage:  python3 evals/run_p6.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import hashlib
import json
import re
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "package.json")
INSTALL_SH = os.path.join(ROOT, "install.sh")

# The three directories that must be byte-identical whichever path installed
# them (plan A8 names skill/, and the same argument applies to the code that
# reads it).
PARITY_DIRS = ("skill", "bin", "lib")

results = []


def check(assertion, name, ok, detail=""):
    results.append((assertion, name, bool(ok), detail))


# ------------------------------------------------------------------ helpers


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hashes(root, subdirs=PARITY_DIRS):
    """{relative path: sha256} for every file under the given subdirectories."""
    out = {}
    for subdir in subdirs:
        base = os.path.join(root, subdir)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                out[os.path.relpath(full, root)] = sha256_file(full)
    return out


def run_install(args, home, url=None, sha256=None, extra_env=None, path=None, script=None):
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("SPEAKINGWORDS_PREFIX", None)
    env.pop("SPEAKINGWORDS_BIN_DIR", None)
    env.pop("SPEAKINGWORDS_URL", None)
    env.pop("SPEAKINGWORDS_SHA256", None)
    if url is not None:
        env["SPEAKINGWORDS_URL"] = url
    if sha256 is not None:
        env["SPEAKINGWORDS_SHA256"] = sha256
    if path is not None:
        env["PATH"] = path
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        # Absolute path: the PATH-stripping tests below would otherwise hide
        # the shell itself.
        ["/bin/sh", script or INSTALL_SH] + args,
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
    )


def make_home():
    return tempfile.mkdtemp(prefix="speakingwords-p6-home-")


def pinless_install_sh():
    """A copy of install.sh with the release pins blanked.

    Since 41d19fb the script bakes in the published tarball URL and its SHA-256,
    so the "no URL" / "no checksum" refusals are unreachable through env vars
    alone (`:-` treats empty as unset). The refusals still guard the case that
    matters — a maintainer cutting a release without setting the pins — so they
    are tested against a copy where the pins are empty, which is exactly that
    machine state.
    """
    text = open(INSTALL_SH, encoding="utf-8").read()
    text = re.sub(r'^DEFAULT_URL=.*$', 'DEFAULT_URL=""', text, count=1, flags=re.M)
    text = re.sub(r'^DEFAULT_SHA256=.*$', 'DEFAULT_SHA256=""', text, count=1, flags=re.M)
    fd, path = tempfile.mkstemp(prefix="speakingwords-p6-pinless-", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def app_dir(home):
    return os.path.join(home, ".speakingwords", "app")


def link_path(home):
    return os.path.join(home, ".local", "bin", "speakingwords")


# --------------------------------------------------------------- build stage


def build_tarball(workdir):
    """`npm pack` the repo into workdir; return (tarball path, extracted tree)."""
    proc = subprocess.run(
        ["npm", "pack", "--silent", "--pack-destination", workdir],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None, None, proc.stderr.strip() or proc.stdout.strip()

    tarballs = [f for f in os.listdir(workdir) if f.endswith(".tgz")]
    if len(tarballs) != 1:
        return None, None, "expected one .tgz, got %r" % tarballs
    tarball = os.path.join(workdir, tarballs[0])

    extract_dir = os.path.join(workdir, "npm-extract")
    os.makedirs(extract_dir, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tf:
        members = tf.getnames()
        try:
            tf.extractall(extract_dir, filter="data")
        except TypeError:  # Python < 3.12 has no filter kwarg
            tf.extractall(extract_dir)
    return tarball, (os.path.join(extract_dir, "package"), members), None


# ------------------------------------------------------------------- gates


def eval_package_manifest():
    with open(PKG, "r", encoding="utf-8") as fh:
        pkg = json.load(fh)

    check("PKG", "license is MIT", pkg.get("license") == "MIT", pkg.get("license"))
    check("PKG", "LICENSE file exists", os.path.isfile(os.path.join(ROOT, "LICENSE")))
    check("PKG", "README.md exists", os.path.isfile(os.path.join(ROOT, "README.md")))
    check("PKG", "engines pins node >= 18",
          pkg.get("engines", {}).get("node") == ">=18", pkg.get("engines"))
    check("PKG", "description is non-empty", bool(pkg.get("description", "").strip()))
    check("PKG", "keywords are present", len(pkg.get("keywords") or []) >= 3)

    files = pkg.get("files") or []
    check("PKG", "files allowlist is exactly bin/, lib/, skill/, README.md, LICENSE",
          sorted(files) == sorted(["bin/", "lib/", "skill/", "README.md", "LICENSE"]),
          repr(files))
    check("PKG", "install.sh is not in the files allowlist", "install.sh" not in files)

    scripts = pkg.get("scripts") or {}
    check("PKG", "eval:p6 is wired", scripts.get("eval:p6", "").endswith("run_p6.py"),
          scripts.get("eval:p6"))
    check("PKG", "eval:p6 runs as part of npm run eval",
          "eval:p6" in scripts.get("eval:deterministic", ""),
          scripts.get("eval:deterministic"))

    licence_text = open(os.path.join(ROOT, "LICENSE"), encoding="utf-8").read()
    check("PKG", "LICENSE is MIT for speakingwords contributors",
          "MIT License" in licence_text and "speakingwords contributors" in licence_text)


def eval_tarball_contents(members):
    """The tarball ships the allowlist and nothing else."""
    rel = sorted(m[len("package/"):] for m in members
                 if m.startswith("package/") and m != "package/")
    # tarfile lists directories too on some producers; keep files only.
    rel = [r for r in rel if r and not r.endswith("/")]

    for required in ("package.json", "README.md", "LICENSE",
                     "bin/speakingwords.js", "skill/SKILL.md",
                     "skill/refs/lexicon.md", "skill/scripts/lint.py",
                     "lib/adapters.js", "lib/hooks.js"):
        check("PKG", "tarball ships %s" % required, required in rel, repr(rel[:40]))

    forbidden_prefixes = ("plan/", "evals/", "node_modules/", ".git/", ".claude/", ".serena/")
    leaked = [r for r in rel if r.startswith(forbidden_prefixes)]
    check("PKG", "tarball has no plan/ or evals/", not leaked, repr(leaked))

    check("PKG", "tarball has no install.sh", "install.sh" not in rel)

    junk = [r for r in rel
            if r.endswith(".bak")
            or os.path.basename(r) in ("hits.jsonl", "pref.json", ".DS_Store")]
    check("PKG", "tarball has no .bak, hits.jsonl or pref.json", not junk, repr(junk))

    unexpected = [r for r in rel
                  if not (r.startswith(("bin/", "lib/", "skill/"))
                          or r in ("package.json", "README.md", "LICENSE"))]
    check("PKG", "tarball contains only allowlisted paths", not unexpected, repr(unexpected))


def eval_curl_install(tarball, npm_tree):
    """The happy path: verified download, extracted tree, working symlink."""
    home = make_home()
    try:
        digest = sha256_file(tarball)
        proc = run_install([], home, url="file://" + tarball, sha256=digest)
        check("A8", "install.sh exits 0 on a verified tarball",
              proc.returncode == 0, proc.stderr.strip()[:400])
        check("A8", "install.sh reports the checksum as verified",
              "checksum" in proc.stdout and "verified" in proc.stdout, proc.stdout[:300])

        installed = app_dir(home)
        check("A8", "install.sh created ~/.speakingwords/app",
              os.path.isdir(installed), installed)

        link = link_path(home)
        check("A8", "install.sh symlinked speakingwords into ~/.local/bin",
              os.path.islink(link), link)
        if os.path.islink(link):
            check("A8", "the symlink points at the installed bin",
                  os.path.realpath(link) ==
                  os.path.realpath(os.path.join(installed, "bin", "speakingwords.js")),
                  os.readlink(link))

        # --- A8 proper: three trees, one set of hashes ---
        repo_hashes = tree_hashes(ROOT)
        npm_hashes = tree_hashes(npm_tree)
        curl_hashes = tree_hashes(installed)

        check("A8", "repo tree is non-empty (sanity)", len(repo_hashes) > 0)
        check("A8", "npm tree file list == repo file list",
              sorted(npm_hashes) == sorted(repo_hashes),
              repr(set(repo_hashes) ^ set(npm_hashes)))
        check("A8", "cURL tree file list == npm file list",
              sorted(curl_hashes) == sorted(npm_hashes),
              repr(set(curl_hashes) ^ set(npm_hashes)))

        npm_diff = [k for k in npm_hashes if repo_hashes.get(k) != npm_hashes[k]]
        check("A8", "npm-installed skill/ bin/ lib/ checksums == repo",
              not npm_diff, repr(npm_diff))

        curl_diff = [k for k in curl_hashes if npm_hashes.get(k) != curl_hashes[k]]
        check("A8", "cURL-installed skill/ bin/ lib/ checksums == npm",
              not curl_diff, repr(curl_diff))

        # Say the assertion in its own words, once, so a regression names itself.
        check("A8", "cURL install and npm install are byte-identical",
              curl_hashes == npm_hashes == repo_hashes,
              "%d files compared" % len(repo_hashes))

        # --- the installed CLI actually runs, through the symlink ---
        with open(PKG, "r", encoding="utf-8") as fh:
            expected_version = json.load(fh)["version"]
        run = subprocess.run([link, "version"], capture_output=True, text=True,
                             env=dict(os.environ, HOME=home))
        check("A8", "installed CLI runs through the symlink",
              run.returncode == 0, run.stderr.strip()[:300])
        check("A8", "installed `speakingwords version` prints %s" % expected_version,
              run.stdout.strip() == expected_version, run.stdout.strip())

        # --- reinstall is safe: same tree, no duplication ---
        again = run_install([], home, url="file://" + tarball, sha256=digest)
        check("A8", "re-running install.sh exits 0", again.returncode == 0,
              again.stderr.strip()[:300])
        check("A8", "re-running install.sh leaves an identical tree",
              tree_hashes(installed) == curl_hashes)
        stray = [d for d in os.listdir(os.path.join(home, ".speakingwords"))
                 if d != "app"]
        check("A8", "re-running install.sh leaves no staging directory behind",
              not stray, repr(stray))

        return home, digest
    except Exception:
        shutil.rmtree(home, ignore_errors=True)
        raise


def eval_uninstall(home):
    """--uninstall reverses exactly what install did."""
    try:
        proc = run_install(["--uninstall"], home)
        check("PKG", "--uninstall exits 0", proc.returncode == 0, proc.stderr.strip()[:300])
        check("PKG", "--uninstall removes the symlink",
              not os.path.lexists(link_path(home)))
        check("PKG", "--uninstall removes the app tree", not os.path.exists(app_dir(home)))
        check("PKG", "--uninstall says what it removed",
              "removed" in proc.stdout.lower(), proc.stdout[:300])

        # Second run must be a no-op, not an error.
        again = run_install(["--uninstall"], home)
        check("PKG", "--uninstall on a clean machine exits 0 and says so",
              again.returncode == 0 and "Nothing to uninstall" in again.stdout,
              again.stdout[:200])
    finally:
        shutil.rmtree(home, ignore_errors=True)


def eval_checksum_refusals(tarball):
    """A bad or absent checksum must stop the install dead."""
    digest = sha256_file(tarball)
    url = "file://" + tarball

    # --- wrong checksum ---
    home = make_home()
    try:
        bad = "0" * 64
        proc = run_install([], home, url=url, sha256=bad)
        check("PKG", "bad checksum exits non-zero", proc.returncode != 0, proc.stdout[:200])
        check("PKG", "bad checksum names the mismatch",
              "mismatch" in proc.stderr.lower(), proc.stderr[:300])
        check("PKG", "bad checksum installs nothing", not os.path.exists(app_dir(home)))
        check("PKG", "bad checksum links nothing", not os.path.lexists(link_path(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    # The release pins make "no URL" / "no checksum" unreachable through env
    # vars on the shipped script, so these refusals run against a pin-blanked
    # copy — the maintainer-forgot-the-pins machine state they exist to catch.
    pinless = pinless_install_sh()

    # --- no checksum, no --insecure ---
    home = make_home()
    try:
        proc = run_install([], home, url=url, script=pinless)
        check("PKG", "missing checksum exits non-zero", proc.returncode != 0, proc.stdout[:200])
        check("PKG", "missing checksum points at --insecure",
              "--insecure" in proc.stderr, proc.stderr[:300])
        check("PKG", "missing checksum installs nothing", not os.path.exists(app_dir(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)

    # --- no URL at all: no invented default ---
    home = make_home()
    try:
        proc = run_install([], home, sha256=digest, script=pinless)
        check("PKG", "no URL exits non-zero", proc.returncode != 0, proc.stdout[:200])
        check("PKG", "no URL explains that one must be supplied",
              "SPEAKINGWORDS_URL" in proc.stderr, proc.stderr[:300])
    finally:
        shutil.rmtree(home, ignore_errors=True)

    # --- --insecure is the only way through without a checksum ---
    home = make_home()
    try:
        proc = run_install(["--insecure"], home, url=url, script=pinless)
        check("PKG", "--insecure installs without a checksum",
              proc.returncode == 0, proc.stderr.strip()[:300])
        check("PKG", "--insecure says the tarball was not verified",
              "SKIPPED" in proc.stdout, proc.stdout[:300])
        check("PKG", "--insecure still lands the app tree",
              os.path.isdir(app_dir(home)))
        os.unlink(pinless)
    finally:
        shutil.rmtree(home, ignore_errors=True)


def eval_node_gate(tarball):
    """Node < 18, and no Node at all, both refuse before touching anything."""
    digest = sha256_file(tarball)
    url = "file://" + tarball

    # --- a stub `node` that reports v16 ---
    home = make_home()
    stub_dir = tempfile.mkdtemp(prefix="speakingwords-p6-stub-")
    try:
        stub = os.path.join(stub_dir, "node")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho v16.20.2\n")
        os.chmod(stub, 0o755)
        # Keep the real tool directories on PATH, but ahead of them put the stub.
        proc = run_install([], home, url=url, sha256=digest,
                           path=stub_dir + ":/usr/bin:/bin:/usr/sbin:/sbin")
        check("PKG", "Node 16 refuses the install", proc.returncode != 0, proc.stdout[:200])
        check("PKG", "Node 16 refusal names the >= 18 requirement",
              ">= 18" in proc.stderr or ">=18" in proc.stderr, proc.stderr[:300])
        check("PKG", "Node 16 refusal installs nothing", not os.path.exists(app_dir(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(stub_dir, ignore_errors=True)

    # --- no node on PATH at all ---
    home = make_home()
    empty = tempfile.mkdtemp(prefix="speakingwords-p6-empty-")
    try:
        proc = run_install([], home, url=url, sha256=digest, path=empty)
        check("PKG", "a machine without Node refuses the install",
              proc.returncode != 0, proc.stdout[:200])
        check("PKG", "the no-Node message says Node is required",
              "Node.js" in proc.stderr, proc.stderr[:300])
        check("PKG", "the no-Node refusal installs nothing",
              not os.path.exists(app_dir(home)))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)


def eval_install_sh_shape():
    """Discipline checks on the script itself — POSIX sh, no bashisms."""
    text = open(INSTALL_SH, encoding="utf-8").read()
    check("PKG", "install.sh has a POSIX sh shebang",
          text.startswith("#!/bin/sh"), text.splitlines()[0] if text else "")
    check("PKG", "install.sh sets -eu", "set -eu" in text)
    # Matched as syntax, not as substrings — the script's own comments and help
    # text are allowed to say the words "local" and "[[".
    for pattern, label in (
        (r"(?m)^\s*(?:if\s+|elif\s+|while\s+)?\[\[", "[[ ]] test"),
        (r"(?m)^\s*local\s+\w", "local"),
        (r"(?m)^\s*function\s+\w+\s*(?:\(\))?\s*\{", "function keyword"),
        (r"(?m)^\s*\w+=\(", "array assignment"),
    ):
        check("PKG", "install.sh avoids the %s bashism" % label,
              re.search(pattern, text) is None)
    # Since 41d19fb the maintainer sets both pins at release time; the release
    # gate is that they exist and agree with the version being shipped.
    pkg_version = json.load(open(os.path.join(ROOT, "package.json")))["version"]
    url_match = re.search(r'^DEFAULT_URL="([^"]*)"', text, flags=re.M)
    sha_match = re.search(r'^DEFAULT_SHA256="([^"]*)"', text, flags=re.M)
    check("PKG", "install.sh pins the published tarball for this version",
          url_match is not None
          and url_match.group(1)
          == "https://registry.npmjs.org/speakingwords/-/speakingwords-%s.tgz" % pkg_version,
          url_match.group(1) if url_match else "no DEFAULT_URL")
    check("PKG", "install.sh pins a SHA-256 for the tarball",
          sha_match is not None and re.fullmatch(r"[0-9a-f]{64}", sha_match.group(1) or "") is not None,
          sha_match.group(1) if sha_match else "no DEFAULT_SHA256")
    check("PKG", "install.sh version hint matches package.json",
          'VERSION_HINT="%s"' % pkg_version in text)
    proc = subprocess.run(["sh", "-n", INSTALL_SH], capture_output=True, text=True)
    check("PKG", "install.sh parses under sh -n", proc.returncode == 0, proc.stderr[:300])

    proc = subprocess.run(["sh", INSTALL_SH, "--help"], capture_output=True, text=True)
    check("PKG", "install.sh --help exits 0", proc.returncode == 0, proc.stderr[:200])
    check("PKG", "install.sh --help documents --uninstall and --insecure",
          "--uninstall" in proc.stdout and "--insecure" in proc.stdout)


def eval_readme_reflects_reality():
    """The README may only claim what the CLI actually does (plan §10)."""
    readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    help_out = subprocess.run(
        ["node", os.path.join(ROOT, "bin", "speakingwords.js"), "--help"],
        capture_output=True, text=True,
    ).stdout

    for command in ("init", "status", "update", "unhook", "version"):
        check("DOC", "README documents `%s`" % command,
              "speakingwords %s" % command in readme)
        check("DOC", "`%s` is a real command" % command,
              "speakingwords %s" % command in help_out)

    for flag in ("--memory", "--hook", "--agent", "--scope", "--voice", "--yes"):
        check("DOC", "README's %s flag exists in --help" % flag, flag in help_out)

    check("DOC", "README documents the unset alias", "unset" in readme and "unset" in help_out)
    check("DOC", "README states both install paths",
          "npm i -g speakingwords" in readme and "curl -fsSL" in readme)
    check("DOC", "README explains the one-bounce guard",
          "One bounce" in readme and "stop_hook_active" in readme)
    check("DOC", "README explains fail-open", "Fail-open" in readme or "fail-open" in readme)
    check("DOC", "README states the Codex audit-only fallback below v0.124.0",
          "v0.124.0" in readme and "audit-only" in readme)
    check("DOC", "README states the Codex trust step", "trust" in readme.lower())
    # Whitespace-normalised: the phrase may wrap across a line break in the
    # README source without changing what the reader sees.
    check("DOC", "README states the checksum refusal",
          "refuses to install without a checksum" in re.sub(r"[\s*]+", " ", readme))
    check("DOC", "README says memory mode is suggestive, not enforced",
          "suggestive" in readme)

    # Rule counts in the README must match the shipped lexicon.
    lexicon = open(os.path.join(ROOT, "skill", "refs", "lexicon.md"), encoding="utf-8").read()
    strip_rows = [ln for ln in lexicon.splitlines() if ln.startswith("| strip-")]
    lang_rows = [ln for ln in lexicon.splitlines() if ln.startswith("| lang-")]
    check("DOC", "README's strip-rule count matches the lexicon (%d)" % len(strip_rows),
          "%d shipped by default" % len(strip_rows) in readme)
    check("DOC", "README's language-rule count matches the lexicon (%d)" % len(lang_rows),
          "%d shipped" % len(lang_rows) in readme)

    # No command the CLI does not have.
    check("DOC", "README claims no command the CLI lacks",
          "speakingwords uninstall" not in readme and "speakingwords doctor" not in readme)


# -------------------------------------------------------------------- main


def main():
    eval_package_manifest()
    eval_install_sh_shape()
    eval_readme_reflects_reality()

    workdir = tempfile.mkdtemp(prefix="speakingwords-p6-build-")
    try:
        tarball, extracted, error = build_tarball(workdir)
        if error:
            check("PKG", "npm pack builds a tarball", False, error)
        else:
            npm_tree, members = extracted
            check("PKG", "npm pack builds a tarball", True)
            eval_tarball_contents(members)
            home, _digest = eval_curl_install(tarball, npm_tree)
            eval_uninstall(home)
            eval_checksum_refusals(tarball)
            eval_node_gate(tarball)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    grouped = {}
    for assertion, name, ok, detail in results:
        grouped.setdefault(assertion, []).append((name, ok, detail))

    out = ["", "speakingwords — Phase 6 deterministic evals", ""]
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
    out.append("PHASE 6 PASS" if not failures else "PHASE 6 FAIL")
    out.append("")
    sys.stdout.write("\n".join(out))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
