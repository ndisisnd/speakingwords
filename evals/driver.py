#!/usr/bin/env python3
"""E8/E9 recorded run driver. Model calls via `claude -p`."""
import json, os, subprocess, sys, threading
from concurrent.futures import ThreadPoolExecutor

ROOT = "/Users/andychan/Desktop/Drive/code/speakingwords"
SCRATCH = os.path.dirname(os.path.abspath(__file__))
MODEL = "claude-sonnet-5"
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(ROOT, "evals"))
import run_p9 as p9
import run_p10 as p10
import harness_checks as hc

SKILL = open(os.path.join(ROOT, "skill", "SKILL.md"), encoding="utf-8").read()
MANIFEST = json.load(open(os.path.join(ROOT, "evals", "fixtures", "manifest.json"), encoding="utf-8"))

_lock = threading.Lock()
_done = [0]


def call(prompt, tag, retries=1):
    for attempt in range(retries + 1):
        try:
            p = subprocess.run(
                ["claude", "-p", "--model", MODEL, "--max-turns", "6", prompt],
                capture_output=True, text=True, timeout=300,
            )
            if p.returncode == 0 and p.stdout.strip():
                with _lock:
                    _done[0] += 1
                    sys.stderr.write("\r%d done   " % _done[0])
                return p.stdout.strip(), None
            err = "rc=%d stderr=%s" % (p.returncode, p.stderr.strip()[:200])
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
    with _lock:
        _done[0] += 1
    return None, "%s: %s" % (tag, err)


def fanout(jobs):
    """jobs: list of (key, prompt). Returns dict key -> (text, err)."""
    out = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(call, pr, k): k for k, pr in jobs}
        for f in futs:
            pass
        for f, k in futs.items():
            out[k] = f.result()
    return out


# ------------------------------------------------------------------ E8

LEVEL_LABEL = {"low": "low", "med": "med", "high": "high"}


def e8_rewrite_prompt(text, level):
    return (
        "You are the speakingwords rewrite pass. Below is the skill contract you must "
        "obey, then a reply that was written without it. Rewrite the reply so it obeys "
        "the contract at voice `convo` and conciseness `%s`.\n\n"
        "Output ONLY the rewritten reply. No preamble, no explanation, no code fence "
        "around the whole thing.\n\n"
        "===== BEGIN skill/SKILL.md =====\n%s\n===== END skill/SKILL.md =====\n\n"
        "===== BEGIN reply to rewrite (voice convo, conciseness %s) =====\n%s\n"
        "===== END reply to rewrite =====\n" % (level, SKILL, level, text)
    )


def e8_judge_prompt(before, after):
    return (
        "You are a strict fact-loss judge for a text rewrite eval.\n\n"
        "Question: does every fact, number, file path, command and code block in the "
        "ORIGINAL still appear in the REWRITE, unchanged in meaning?\n\n"
        "One thing is NOT a loss: an enumeration the reader can retrieve elsewhere "
        "(the files in a diff, the rules in a lexicon table) collapsed to its function, "
        "its count and a pointer. A missing count or a missing pointer IS a loss, and so "
        "is anything the pointer does not carry.\n\n"
        "Judge fact preservation ONLY. Do not judge style, tone, length or register.\n\n"
        "Answer with a single JSON object and nothing else:\n"
        '{\"loss\": true|false, \"reason\": \"one or two sentences\"}\n\n'
        "===== ORIGINAL =====\n%s\n===== REWRITE =====\n%s\n===== END =====\n"
        % (before, after)
    )


def parse_json(text):
    if text is None:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        return json.loads(t[i:j + 1])
    except ValueError:
        return None


def run_e8():
    paths = p9.e8_fixtures(MANIFEST)
    fixtures = {os.path.basename(p): open(p, encoding="utf-8").read() for p in paths}
    jobs = []
    for name, text in fixtures.items():
        for level in p9.LEVELS:
            jobs.append(((name, level), e8_rewrite_prompt(text, level)))
    sys.stderr.write("E8: %d rewrites\n" % len(jobs))
    res = fanout(jobs)
    rewrites, errors = {}, []
    for (name, level), (txt, err) in res.items():
        if err:
            errors.append(err)
        rewrites["%s|%s" % (name, level)] = txt

    # medians via the repo's own arithmetic
    def rewriter(text, level):
        for name, t in fixtures.items():
            if t == text:
                r = rewrites.get("%s|%s" % (name, level))
                return r if r is not None else text
        raise KeyError("unknown fixture text")

    if errors:
        sys.stderr.write("\nrewrite errors:\n" + "\n".join(errors[:10]) + "\n")
    usable = [p for p in paths
              if all(rewrites.get("%s|%s" % (os.path.basename(p), l)) for l in p9.LEVELS)]
    if not usable:
        raise SystemExit("no usable fixtures; %d rewrite errors" % len(errors))
    medians = p9.e8_measure(usable, rewriter)
    bands = {l: p9.in_band(medians[l], l) for l in p9.LEVELS}

    per_pair = {}
    for p in usable:
        name = os.path.basename(p)
        for level in p9.LEVELS:
            per_pair["%s|%s" % (name, level)] = p9.reduction(
                fixtures[name], rewrites["%s|%s" % (name, level)])

    # judge
    jjobs = [(k, e8_judge_prompt(fixtures[k.split("|")[0]], rewrites[k]))
             for k in sorted(per_pair)]
    sys.stderr.write("\nE8: %d fact-loss judgements\n" % len(jjobs))
    jres = fanout(jjobs)
    losses, judge_errors, judgements = [], [], {}
    for k, (txt, err) in jres.items():
        if err:
            judge_errors.append(err)
            continue
        obj = parse_json(txt)
        if obj is None:
            judge_errors.append("%s: unparseable judge output" % k)
            continue
        judgements[k] = obj
        if obj.get("loss"):
            losses.append({"pair": k, "reason": obj.get("reason", "")})

    # A27: convo never collapses into terse, at ALL THREE levels.
    # Uses the repo's harness_checks.convo_prose_gate(), not the old
    # terse-prose-block linter proxy (vacuous on bullet lists).
    gate_pairs = []
    bullet_counts = {}
    for p in usable:
        name = os.path.basename(p)
        for level in p9.LEVELS:
            after = rewrites["%s|%s" % (name, level)]
            gate_pairs.append((name, "convo", level, fixtures[name], after))
            bullet_counts["%s|%s" % (name, level)] = {
                "before": hc.bullet_line_count(fixtures[name]),
                "after": hc.bullet_line_count(after),
            }
    a27_failures = hc.convo_prose_gate(gate_pairs)
    prose_fails = sorted({f["pair"] for f in a27_failures})

    return {
        "a27_gate": {
            "pairs_checked": len(gate_pairs),
            "levels": list(p9.LEVELS),
            "failures": a27_failures,
            "failure_count": len(a27_failures),
            "pass": not a27_failures,
        },
        "bullet_counts": bullet_counts,
        "fixtures": len(fixtures),
        "rewrites_attempted": len(jobs),
        "rewrites_ok": sum(1 for v in rewrites.values() if v),
        "fixtures_usable": len(usable),
        "medians": medians,
        "bands": {l: list(p9.E8_BANDS[l]) for l in p9.LEVELS},
        "in_band": bands,
        "per_pair_reduction": per_pair,
        "judgements": judgements,
        "losses": losses,
        "judge_errors": judge_errors,
        "rewrite_errors": errors,
        "convo_high_prose_failures": prose_fails,
        "_rewrites": rewrites,
    }


# ------------------------------------------------------------------ E9

E9_LEVEL = "med"


def e9_reply_prompt(prompt, voice):
    return (
        "You are answering a colleague's question in a Slack DM, under the "
        "speakingwords voice contract below. Obey it: voice `%s`, conciseness `%s`.\n\n"
        "Output ONLY the reply. No preamble.\n\n"
        "===== BEGIN skill/SKILL.md =====\n%s\n===== END skill/SKILL.md =====\n\n"
        "===== QUESTION =====\n%s\n===== END QUESTION =====\n" % (voice, E9_LEVEL, SKILL, prompt)
    )


def e9_bounce_prompt(prompt, voice, reply):
    return (
        "You are the speakingwords rewrite pass. The reply below was bounced. Rewrite it "
        "so it obeys the contract at voice `%s`, conciseness `%s`, losing no content.\n\n"
        "Output ONLY the rewritten reply. No preamble.\n\n"
        "===== BEGIN skill/SKILL.md =====\n%s\n===== END skill/SKILL.md =====\n\n"
        "===== QUESTION THAT WAS ASKED =====\n%s\n"
        "===== BOUNCED REPLY =====\n%s\n===== END =====\n" % (voice, E9_LEVEL, SKILL, prompt, reply)
    )


def e9_judge_prompt(axis, question, reply):
    q = p10.e9_register_question(E9_LEVEL) if axis == "register" else p10.E9_RUBRIC["fidelity"]
    if axis == "fidelity":
        extra = ("There is no separate original text here: judge whether the reply keeps its "
                 "own technical substance concrete — specific facts, numbers, file paths, "
                 "commands and code blocks where the answer needs them — rather than being "
                 "vague or hand-waving. An enumeration collapsed to its function plus a count "
                 "plus a pointer passes; a missing count or pointer is a loss.")
    else:
        extra = "Judge register ONLY. Do not penalise the reply for technical content."
    return (
        "You are judging one axis of a reply. Judge ONLY this axis.\n\n"
        "AXIS (%s): %s\n\n%s\n\n"
        "Answer with a single JSON object and nothing else:\n"
        '{\"pass\": true|false, \"reason\": \"one or two sentences\"}\n\n'
        "===== QUESTION =====\n%s\n===== REPLY =====\n%s\n===== END =====\n"
        % (axis, q, extra, question, reply)
    )


def judge_replies(items):
    """items: list of (key, question, reply). Returns key -> {axis: obj}"""
    jobs = []
    for key, q, r in items:
        for axis in p10.E9_AXES:
            jobs.append(((key, axis), e9_judge_prompt(axis, q, r)))
    res = fanout(jobs)
    out, errs = {}, []
    for (key, axis), (txt, err) in res.items():
        if err:
            errs.append(err)
            continue
        obj = parse_json(txt)
        if obj is None:
            errs.append("%s/%s: unparseable" % (key, axis))
            continue
        out.setdefault(key, {})[axis] = obj
    return out, errs


def run_e9():
    prompts = MANIFEST["e9_prompts"]
    jobs = []
    for i, pr in enumerate(prompts):
        for voice in p10.VOICES:
            jobs.append(("p%02d|%s" % (i + 1, voice), e9_reply_prompt(pr, voice)))
    sys.stderr.write("\nE9: %d replies\n" % len(jobs))
    res = fanout(jobs)
    replies, errors = {}, []
    for k, (txt, err) in res.items():
        if err:
            errors.append(err)
        replies[k] = txt

    def q_of(key):
        return prompts[int(key.split("|")[0][1:]) - 1]

    items = [(k, q_of(k), v) for k, v in sorted(replies.items()) if v]
    sys.stderr.write("\nE9: %d first-reply judgements\n" % (len(items) * 2))
    judged, jerrs = judge_replies(items)

    first = {}
    for k in sorted(replies):
        if k not in judged or len(judged[k]) < 2:
            first[k] = None
            continue
        first[k] = {a: bool(judged[k][a].get("pass")) for a in p10.E9_AXES}

    scored = [v for v in first.values() if v]
    first_rate = p10.e9_rate(scored)
    axis_rates = {a: (sum(1 for v in scored if v[a]) / float(len(scored)) if scored else 0.0)
                  for a in p10.E9_AXES}

    # bounce the failures once
    failing = [k for k, v in first.items() if v and not p10.e9_pass(v)]
    bounced, bjudged, berrs = {}, {}, []
    if failing:
        bjobs = [(k, e9_bounce_prompt(q_of(k), k.split("|")[1], replies[k])) for k in failing]
        sys.stderr.write("\nE9: %d bounce rewrites\n" % len(bjobs))
        bres = fanout(bjobs)
        for k, (txt, err) in bres.items():
            if err:
                berrs.append(err)
            bounced[k] = txt
        bitems = [(k, q_of(k), v) for k, v in sorted(bounced.items()) if v]
        sys.stderr.write("\nE9: %d re-judgements\n" % (len(bitems) * 2))
        bj, be = judge_replies(bitems)
        berrs += be
        for k in failing:
            if k in bj and len(bj[k]) == 2:
                bjudged[k] = {a: bool(bj[k][a].get("pass")) for a in p10.E9_AXES}

    post = {}
    for k, v in first.items():
        if not v:
            continue
        post[k] = v if p10.e9_pass(v) else bjudged.get(k, {"register": False, "fidelity": False})
    post_rate = p10.e9_rate(list(post.values()))

    return {
        "level": E9_LEVEL,
        "prompts": len(prompts),
        "replies_attempted": len(jobs),
        "replies_ok": sum(1 for v in replies.values() if v),
        "first_scores": first,
        "first_reasons": {k: {a: judged[k][a].get("reason", "") for a in judged[k]}
                          for k in judged},
        "first_rate": first_rate,
        "axis_rates": axis_rates,
        "first_gate": p10.E9_FIRST_REPLY_GATE,
        "post_gate": p10.E9_POST_BOUNCE_GATE,
        "failing_first": sorted(failing),
        "bounce_scores": bjudged,
        "post_rate": post_rate,
        "reply_errors": errors,
        "judge_errors": jerrs + berrs,
        "_replies": replies,
        "_bounced": bounced,
    }


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    out = {}
    if which in ("e8", "both"):
        out["e8"] = run_e8()
        json.dump(out["e8"], open(os.path.join(SCRATCH, "e8_raw.json"), "w"), indent=1)
    if which in ("e9", "both"):
        out["e9"] = run_e9()
        json.dump(out["e9"], open(os.path.join(SCRATCH, "e9_raw.json"), "w"), indent=1)
    sys.stderr.write("\ndone\n")
