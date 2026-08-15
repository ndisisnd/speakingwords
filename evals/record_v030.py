#!/usr/bin/env python3
"""E11/E12 recording driver — v0.3.0 release pass.

Rides on record_rig.py: same real-install method, same auth (user-minted token
in ~/.speakingwords-eval-token, never printed, never written to output), same
model as the rig2 recordings so numbers stay comparable.

E11 — STE register (plan v0.3.0 W5). The E8 fixture set is rewritten inside
`ste`-register hook installs, at both voices. Recorded scope: the `high` level
only — register and level are orthogonal by design (the register table carries
no `active at` level column), so the level axis adds cost without adding
information about the register. The record says so.

  Deterministic gate, checked by the shipped linter itself (`lint.py --register
  ste`): zero `ste-contraction` and zero `ste-long-sentence` violations in the
  rewrites. The linter is the checker on purpose — a private re-implementation
  here could drift from what ships.
  Judged gates: active-voice/imperative and technical fidelity as separate
  axes, >=85% both on first output; fact loss zero via the E8 judge.

E12 — both-mode prevention (plan v0.3.0 W6). The same prompts run against a
hook-only install and a `both` install, same voice/level, and the Stop-bounce
rate is compared. A bounce is read from the session transcript (more than one
assistant turn), not from hits.jsonl — hits count violation lines, and one
bounced reply can carry many. Both prompt sets run: the E8 rewrites (the set
the plan names) and the E9 generation prompts (the set rig2 measured real
bounces on), so a zero-zero tie on one set cannot decide the gate alone.

Usage:  python3 evals/record_v030.py [smoke|e11|e12|both]
Output: evals/rig2/e11_raw.json, evals/rig2/e12_raw.json
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)

import record_rig as rig       # noqa: E402
import run_p9 as p9            # noqa: E402
import run_p10 as p10          # noqa: E402
import driver as base          # noqa: E402

LINT = os.path.join(ROOT, "skill", "scripts", "lint.py")
MANIFEST = rig.MANIFEST
E11_LEVEL = "high"
E12_VOICE, E12_LEVEL = "convo", "high"

E11_AXES = ("active_imperative", "fidelity")

E11_RUBRIC = {
    "active_imperative": (
        "The reply is written in the active voice, and instructions are given "
        "as imperatives (\"Install the pump\", not \"The pump should be "
        "installed\"). One instruction per sentence. Articles are kept. "
        "Passive constructions and stacked instructions fail this axis."
    ),
    "fidelity": (
        "Every fact, number, file path, command, caveat and causal link of the "
        "original is still present and unchanged in meaning. Simplified grammar "
        "is expected; simplified content is a failure."
    ),
}


def e11_judge_prompt(axis, before, after):
    return (
        "You are judging one axis of a rewrite. Judge ONLY this axis.\n\n"
        "AXIS (%s): %s\n\n"
        "Answer with a single JSON object and nothing else:\n"
        '{"pass": true|false, "reason": "one or two sentences"}\n\n'
        "===== ORIGINAL =====\n%s\n===== REWRITE =====\n%s\n===== END =====\n"
        % (axis, E11_RUBRIC[axis], before, after)
    )


def lint_ste(text, voice):
    """Run the shipped linter at ste over a rewrite; return its violation list."""
    p = subprocess.run(
        [sys.executable, LINT, "--voice", voice, "--conciseness", E11_LEVEL,
         "--register", "ste"],
        input=text, capture_output=True, text=True, timeout=30,
    )
    try:
        return json.loads(p.stdout).get("violations", [])
    except ValueError:
        return [{"rule": "lint-unreadable", "match": p.stderr[:120], "severity": "error"}]


# ------------------------------------------------------------------------ E11

def run_e11():
    paths = sorted(p9.e8_fixtures(MANIFEST))
    fixtures = {os.path.basename(p): open(p, encoding="utf-8").read() for p in paths}
    installs = {v: rig.provision(v, E11_LEVEL, register="ste") for v in p10.VOICES}

    jobs = []
    for name, text in fixtures.items():
        for voice in p10.VOICES:
            home, proj = installs[voice]
            jobs.append(((name, voice),
                         (rig.E8_PROMPT % text, home, proj, "%s|%s" % (name, voice))))
    sys.stderr.write("E11: %d ste rewrites\n" % len(jobs))
    res = rig.fanout(jobs, rig.session_call)

    rewrites, errors = {}, []
    for (name, voice), (txt, _sid, err) in res.items():
        if err:
            errors.append(err)
        if txt:
            rewrites["%s|%s" % (name, voice)] = txt

    # Deterministic: the shipped linter is the checker.
    lint_hits = {}
    for key, text in sorted(rewrites.items()):
        voice = key.split("|")[1]
        hits = [v for v in lint_ste(text, voice)
                if v["rule"] in ("ste-contraction", "ste-long-sentence")]
        if hits:
            lint_hits[key] = hits
    contraction_hits = sum(1 for hs in lint_hits.values()
                           for h in hs if h["rule"] == "ste-contraction")
    length_hits = sum(1 for hs in lint_hits.values()
                      for h in hs if h["rule"] == "ste-long-sentence")

    # Judged: two axes per rewrite, plus the E8 fact-loss judge.
    jjobs = []
    for key, after in sorted(rewrites.items()):
        before = fixtures[key.split("|")[0]]
        for axis in E11_AXES:
            jjobs.append(((key, axis),
                          (e11_judge_prompt(axis, before, after), "%s/%s" % (key, axis))))
        jjobs.append(((key, "loss"),
                      (base.e8_judge_prompt(before, after), "%s/loss" % key)))
    sys.stderr.write("\nE11: %d judgements\n" % len(jjobs))
    jres = rig.fanout(jjobs, rig.bare_call)

    judged, losses, judge_errors = {}, [], []
    for (key, axis), (txt, err) in jres.items():
        if err:
            judge_errors.append(err)
            continue
        obj = base.parse_json(txt)
        if obj is None:
            judge_errors.append("%s/%s: unparseable" % (key, axis))
            continue
        if axis == "loss":
            if obj.get("loss"):
                losses.append({"pair": key, "reason": obj.get("reason", "")})
        else:
            judged.setdefault(key, {})[axis] = obj

    scored = {k: {a: bool(v[a].get("pass")) for a in E11_AXES}
              for k, v in judged.items() if len(v) == len(E11_AXES)}
    n = len(scored)
    both_axes = sum(1 for v in scored.values() if all(v.values()))
    axis_rates = {a: (sum(1 for v in scored.values() if v[a]) / float(n) if n else 0.0)
                  for a in E11_AXES}

    out = {
        "scope": "E8 fixture set, register=ste, voices=%s, level=%s only "
                 "(register and level are orthogonal; recorded scope trims the "
                 "level axis, not the register)" % (list(p10.VOICES), E11_LEVEL),
        "rewrites_ok": len(rewrites),
        "deterministic": {
            "contraction_hits": contraction_hits,
            "length_hits": length_hits,
            "offending": lint_hits,
        },
        "judged_rate_both_axes": (both_axes / float(n)) if n else 0.0,
        "axis_rates": axis_rates,
        "scores": scored,
        "reasons": {k: {a: judged[k][a].get("reason", "") for a in judged[k]}
                    for k in judged},
        "losses": losses,
        "rewrite_errors": errors,
        "judge_errors": judge_errors,
        "gates": {"contractions": 0, "long_sentences": 0,
                  "judged_both_axes": 0.85, "losses": 0},
        "_rewrites": rewrites,
    }
    json.dump(out, open(os.path.join(rig.RIG, "e11_raw.json"), "w"), indent=1)
    return out


# ------------------------------------------------------------------------ E12

def bounce_run(prompts, mode, tag):
    """Run one prompt list inside one install; return per-key bounce booleans."""
    home, proj = rig.provision(E12_VOICE, E12_LEVEL, mode=mode)
    jobs = []
    for key, prompt, allow_tools in prompts:
        jobs.append(("%s|%s" % (key, mode),
                     (prompt, home, proj, "%s|%s|%s" % (tag, key, mode), 1, allow_tools)))
    res = rig.fanout(jobs, rig.session_call)
    bounced, errors = {}, []
    for key, (txt, sid, err) in res.items():
        if err:
            errors.append(err)
            continue
        texts = rig.assistant_texts(home, proj, sid) if sid else []
        bounced[key] = len(texts) > 1
    return bounced, errors


def run_e12():
    e8 = [("e8-%s" % os.path.basename(p), rig.E8_PROMPT % open(p, encoding="utf-8").read(), True)
          for p in sorted(p9.e8_fixtures(MANIFEST))]
    e9 = [("e9-p%02d" % (i + 1), rig.E9_PROMPT % pr, False)
          for i, pr in enumerate(MANIFEST["e9_prompts"])]
    prompts = e8 + e9

    out = {"voice": E12_VOICE, "level": E12_LEVEL, "prompts": len(prompts)}
    for mode in ("hook", "both"):
        sys.stderr.write("\nE12: %d replies at mode=%s\n" % (len(prompts), mode))
        bounced, errors = bounce_run(prompts, mode, "e12")
        n = len(bounced)
        rate = (sum(1 for v in bounced.values() if v) / float(n)) if n else None
        out[mode] = {
            "replies_ok": n,
            "bounces": sum(1 for v in bounced.values() if v),
            "rate": rate,
            "by_prompt": {k: bool(v) for k, v in sorted(bounced.items())},
            "errors": errors,
        }
        for prefix in ("e8-", "e9-"):
            keys = [k for k in bounced if k.split("|")[0].startswith(prefix)]
            out[mode]["set_%s" % prefix.rstrip("-")] = {
                "n": len(keys),
                "bounces": sum(1 for k in keys if bounced[k]),
            }
    hook_r, both_r = out["hook"]["rate"], out["both"]["rate"]
    out["gate"] = "both strictly lower than hook"
    out["gate_pass"] = (hook_r is not None and both_r is not None and both_r < hook_r)
    json.dump(out, open(os.path.join(rig.RIG, "e12_raw.json"), "w"), indent=1)
    return out


# ---------------------------------------------------------------------- smoke

def smoke():
    """Two calls: one ste rewrite, one judge. Proves auth and the ste install."""
    home, proj = rig.provision("convo", E11_LEVEL, register="ste")
    fix = sorted(p9.e8_fixtures(MANIFEST))[0]
    ftext = open(fix, encoding="utf-8").read()
    txt, _sid, err = rig.session_call(rig.E8_PROMPT % ftext, home, proj, "smoke-e11")
    ok_a = bool(txt) and len(txt) > 30
    hits = lint_ste(txt or "", "convo")
    jtxt, jerr = rig.bare_call(e11_judge_prompt("fidelity", ftext, txt or ""), "smoke-judge")
    ok_b = base.parse_json(jtxt or "") is not None
    report = {"rewrite_ok": ok_a, "rewrite_err": err,
              "ste_lint_hits": [h["rule"] for h in hits],
              "judge_ok": ok_b, "judge_err": jerr}
    print(json.dumps(report, indent=1))
    return ok_a and ok_b


if __name__ == "__main__":
    os.makedirs(rig.RIG, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which == "smoke":
        sys.exit(0 if smoke() else 1)
    if which in ("e11", "both"):
        run_e11()
    if which in ("e12", "both"):
        run_e12()
    sys.stderr.write("\ndone\n")
