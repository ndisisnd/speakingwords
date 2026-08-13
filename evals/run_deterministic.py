#!/usr/bin/env python3
"""Deterministic evals for speakingwords Phase 1.

Runs the two gates that need no model calls:

  E1  Strip-rule recall and false positives.
      - >= 95% of planted rule ids caught across the 50 violation fixtures.
      - ZERO violations across the 50 clean controls (hard gate: a false
        positive bounces a good reply, the worst failure class).

  E6  Latency. Lint the 4,000-word clean fixture 100 times, p95 < 100 ms.

  A6  Exit-code contract. lint.py exits 0 or 2 on every fixture, never anything
      else.

Usage:  python3 evals/run_deterministic.py
Exit:   0 all gates pass, 1 any gate fails.
"""

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LINT = os.path.join(ROOT, "skill", "scripts", "lint.py")
FIXTURES = os.path.join(HERE, "fixtures")
MANIFEST = os.path.join(FIXTURES, "manifest.json")

E1_RECALL_MIN = 0.95
E6_P95_MAX_MS = 100.0
E6_RUNS = 100

PASS = "PASS"
FAIL = "FAIL"


def run_lint(path, voice="convo"):
    proc = subprocess.run(
        [sys.executable, LINT, "--voice", voice, path],
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = {"verdict": "unparseable", "violations": [], "raw": proc.stdout}
    return proc.returncode, payload, proc.stderr


def load_manifest():
    with open(MANIFEST, "r", encoding="utf-8") as fh:
        return json.load(fh)


def eval_e1(manifest, exit_codes):
    planted_total = 0
    caught_total = 0
    misses = []
    for name in sorted(manifest["violations"]):
        expected = set(manifest["violations"][name])
        path = os.path.join(FIXTURES, "violations", name)
        code, payload, _ = run_lint(path)
        exit_codes.add(code)
        found = {v["rule"] for v in payload.get("violations", [])}
        planted_total += len(expected)
        caught_total += len(expected & found)
        for rule in sorted(expected - found):
            misses.append("%s -> %s" % (name, rule))
        if payload.get("verdict") != "violations":
            misses.append("%s -> verdict was %r" % (name, payload.get("verdict")))

    false_positives = []
    for name in sorted(manifest["clean"]):
        path = os.path.join(FIXTURES, "clean", name)
        code, payload, _ = run_lint(path)
        exit_codes.add(code)
        for v in payload.get("violations", []):
            false_positives.append(
                "%s -> %s (%r)" % (name, v["rule"], v.get("match"))
            )

    recall = (caught_total / planted_total) if planted_total else 0.0
    ok = recall >= E1_RECALL_MIN and not false_positives
    return {
        "ok": ok,
        "recall": recall,
        "planted": planted_total,
        "caught": caught_total,
        "misses": misses,
        "false_positives": false_positives,
    }


def eval_e6(manifest, exit_codes):
    path = os.path.join(FIXTURES, manifest["perf"])
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    words = len(text.split())

    # End-to-end timing: the hook pays interpreter startup, lexicon parse and
    # scan on every reply, so the gate is measured on the whole invocation.
    samples = []
    verdict = None
    for _ in range(E6_RUNS):
        t0 = time.perf_counter()
        code, payload, _ = run_lint(path)
        samples.append((time.perf_counter() - t0) * 1000.0)
        exit_codes.add(code)
        verdict = payload.get("verdict")
    samples.sort()
    p95 = samples[int(round(0.95 * (len(samples) - 1)))]

    # Scan-only timing, reported for diagnosis when the gate gets close.
    sys.dont_write_bytecode = True  # keep __pycache__ out of the shipped tree
    sys.path.insert(0, os.path.join(ROOT, "skill", "scripts"))
    import lint as lint_mod

    rules = lint_mod.read_rules()
    scan = []
    for _ in range(E6_RUNS):
        t0 = time.perf_counter()
        lint_mod.lint(text, "convo", rules)
        scan.append((time.perf_counter() - t0) * 1000.0)
    scan.sort()

    return {
        "ok": p95 < E6_P95_MAX_MS,
        "p95_ms": p95,
        "median_ms": samples[len(samples) // 2],
        "scan_p95_ms": scan[int(round(0.95 * (len(scan) - 1)))],
        "words": words,
        "perf_fixture_verdict": verdict,
    }


def eval_a6(exit_codes):
    bad = sorted(c for c in exit_codes if c not in (0, 2))
    return {"ok": not bad, "seen": sorted(exit_codes), "bad": bad}


def main():
    manifest = load_manifest()
    exit_codes = set()

    e1 = eval_e1(manifest, exit_codes)
    e6 = eval_e6(manifest, exit_codes)
    a6 = eval_a6(exit_codes)

    lines = []
    lines.append("speakingwords deterministic evals")
    lines.append("=" * 58)
    lines.append(
        "E1 recall      %s  %d/%d planted rule ids caught (%.1f%%, gate >=95%%)"
        % (PASS if e1["recall"] >= E1_RECALL_MIN else FAIL,
           e1["caught"], e1["planted"], e1["recall"] * 100)
    )
    lines.append(
        "E1 false pos.  %s  %d violations across %d clean controls (gate = 0)"
        % (PASS if not e1["false_positives"] else FAIL,
           len(e1["false_positives"]), len(manifest["clean"]))
    )
    lines.append(
        "E6 latency     %s  p95 %.2f ms end-to-end on %d words, %d runs "
        "(scan only %.2f ms, gate <100 ms)"
        % (PASS if e6["ok"] else FAIL, e6["p95_ms"], e6["words"], E6_RUNS,
           e6["scan_p95_ms"])
    )
    lines.append(
        "A6 exit codes  %s  observed %s (gate: only 0 and 2)"
        % (PASS if a6["ok"] else FAIL, a6["seen"])
    )

    if e1["misses"]:
        lines.append("")
        lines.append("Missed rules:")
        lines.extend("  - " + m for m in e1["misses"])
    if e1["false_positives"]:
        lines.append("")
        lines.append("False positives on clean controls:")
        lines.extend("  - " + m for m in e1["false_positives"])
    if a6["bad"]:
        lines.append("")
        lines.append("Illegal exit codes: %s" % a6["bad"])

    ok = e1["ok"] and e6["ok"] and a6["ok"]
    lines.append("=" * 58)
    lines.append("RESULT: %s" % (PASS if ok else FAIL))
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
