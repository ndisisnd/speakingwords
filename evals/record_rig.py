#!/usr/bin/env python3
"""E8/E9 recording rig — real installed environments, not pasted contracts.

Per plan/speakingwords-v0.2.0-patch1.md and e9_report(): every rewrite/reply
session runs inside a throwaway HOME + project provisioned by the actual
installer (`init --hook`), with the Stop hook and SessionStart injection live
and skill files on disk. The prompt never contains SKILL.md.

Auth: a user-minted subscription token read from ~/.speakingwords-eval-token
at runtime. Never printed, never written to any output file. Sessions run with
the sidecar proxy env stripped (it serves the orchestration session only and
rejects child credentials).

Judge calls reuse the exact judge prompts from the two baseline recordings
(driver.py) so verdicts stay comparable; judges run in a bare temp HOME with
the same token — no install, no user config.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = "/Users/andychan/Desktop/Drive/code/speakingwords"
SCRATCH = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(SCRATCH, "rig2")
MODEL = "claude-sonnet-5"
TOKEN_FILE = os.path.expanduser("~/.speakingwords-eval-token")
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(ROOT, "evals"))
sys.path.insert(0, SCRATCH)
import run_p9 as p9            # noqa: E402
import run_p10 as p10          # noqa: E402
import harness_checks as hc    # noqa: E402
import driver as base          # noqa: E402  (judge prompts + parse_json only)

MANIFEST = json.load(open(os.path.join(ROOT, "evals", "fixtures", "manifest.json"),
                          encoding="utf-8"))

_lock = threading.Lock()
_done = [0]


def _token():
    for line in open(TOKEN_FILE, encoding="utf-8"):
        if line.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
            return line.strip().split("=", 1)[1]
    raise SystemExit("token file has no CLAUDE_CODE_OAUTH_TOKEN line")


def _env(home):
    env = dict(os.environ)
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "CLAUDE_CONFIG_DIR"):
        env.pop(k, None)
    env["HOME"] = home
    env["CLAUDE_CODE_OAUTH_TOKEN"] = _token()
    return env


# ---------------------------------------------------------------- provisioning

def provision(voice, level, mode="hook"):
    """One install per (voice, level, mode): temp HOME + project, real init.

    `mode` is the seam E12 needs (plan v0.3.0 W6): the same prompt set has to run
    against a hook-only install and a `both` install so the two bounce rates are
    comparable. A hook install keeps its old slug and its old assertions, so the
    trees already provisioned for the E8/E9 recordings are not invalidated.

    The SessionStart assertion follows the mode rather than being fixed, because
    at `both` an injector present is a defect, not a healthy install (A31): the
    memory block carries the contract instead, and the rig must be measuring the
    mode as it ships.
    """
    slug = "%s-%s" % (voice, level) if mode == "hook" else "%s-%s-%s" % (voice, level, mode)
    home = os.path.join(RIG, "installs", slug, "home")
    proj = os.path.join(RIG, "installs", slug, "proj")
    if os.path.isdir(os.path.join(proj, ".claude")):
        return home, proj  # already provisioned
    os.makedirs(home, exist_ok=True)
    os.makedirs(proj, exist_ok=True)
    p = subprocess.run(
        ["node", os.path.join(ROOT, "bin", "speakingwords.js"), "init",
         "--%s" % mode, "--agent", "claude", "--scope", "local",
         "--voice", voice, "--conciseness", level],
        cwd=proj, env={**os.environ, "HOME": home},
        capture_output=True, text=True, timeout=60,
    )
    if p.returncode != 0:
        raise SystemExit("provision %s failed: %s" % (slug, p.stderr[:400]))
    settings = json.load(open(os.path.join(proj, ".claude", "settings.json")))
    events = settings.get("hooks", {})
    assert "Stop" in events, "Stop hook not wired: %s" % slug
    if mode == "both":
        assert "SessionStart" not in events, "both mode wired an injector: %s" % slug
        block = os.path.join(proj, "CLAUDE.local.md")
        assert os.path.isfile(block), "both mode wrote no memory block: %s" % slug
    else:
        assert "SessionStart" in events, "injector not wired: %s" % slug
    skill = os.path.join(home, ".claude", "skills", "speakingwords")
    assert os.path.isfile(os.path.join(skill, "SKILL.md")), "skill core missing: %s" % slug
    return home, proj


def hits_path(home):
    return os.path.join(home, ".claude", "skills", "speakingwords", "hits.jsonl")


def hits_count(home):
    try:
        return sum(1 for _ in open(hits_path(home), encoding="utf-8"))
    except OSError:
        return 0


# ------------------------------------------------------------------- sessions

def session_call(prompt, home, proj, tag, retries=1, allow_tools=True):
    """One claude -p session inside an install. Returns (result_text, session_id, err)."""
    for _ in range(retries + 1):
        try:
            p = subprocess.run(
                ["claude", "-p", "--model", MODEL, "--max-turns", "8",
                 "--allowedTools", "Read Glob Grep",
                 "--disallowedTools", "Bash Edit Write WebSearch WebFetch",
                 "--output-format", "json", prompt] if allow_tools else
                ["claude", "-p", "--model", MODEL, "--max-turns", "6",
                 "--tools", "",
                 "--output-format", "json", prompt],
                cwd=proj, env=_env(home), capture_output=True, text=True, timeout=600,
            )
            if p.returncode == 0 and p.stdout.strip():
                obj = json.loads(p.stdout)
                with _lock:
                    _done[0] += 1
                    sys.stderr.write("\r%d calls done" % _done[0])
                return obj.get("result", "").strip(), obj.get("session_id"), None
            err = "rc=%d stderr=%s" % (p.returncode, p.stderr.strip()[:200])
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
    with _lock:
        _done[0] += 1
    return None, None, "%s: %s" % (tag, err)


def bare_call(prompt, tag, retries=1):
    """Judge call: bare temp HOME, no install, token auth. Text out."""
    home = os.path.join(RIG, "judge-home")
    os.makedirs(home, exist_ok=True)
    for _ in range(retries + 1):
        try:
            p = subprocess.run(
                ["claude", "-p", "--model", MODEL, "--max-turns", "2", prompt],
                cwd=home, env=_env(home), capture_output=True, text=True, timeout=300,
            )
            if p.returncode == 0 and p.stdout.strip():
                with _lock:
                    _done[0] += 1
                    sys.stderr.write("\r%d calls done" % _done[0])
                return p.stdout.strip(), None
            err = "rc=%d stderr=%s" % (p.returncode, p.stderr.strip()[:200])
        except Exception as e:  # noqa: BLE001
            err = repr(e)[:200]
    with _lock:
        _done[0] += 1
    return None, "%s: %s" % (tag, err)


def fanout(jobs, fn):
    out = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn, *args): key for key, args in jobs}
        for f, key in futs.items():
            out[key] = f.result()
    return out


def assistant_texts(home, proj, session_id):
    """All assistant text blocks from a session transcript, in order."""
    slug = proj.replace("/", "-")
    path = os.path.join(home, ".claude", "projects", slug, "%s.jsonl" % session_id)
    texts = []
    try:
        for line in open(path, encoding="utf-8"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                t = "".join(c.get("text", "") for c in content
                            if isinstance(c, dict) and c.get("type") == "text")
                if t.strip():
                    texts.append(t)
    except OSError:
        pass
    return texts


# ------------------------------------------------------------------ E8 prompt

E8_PROMPT = ("Rewrite the reply below to follow the installed speakingwords "
             "style contract. Output only the rewritten reply.\n\n"
             "%s")

E9_LEVEL = "high"

# Scenario framing only — names the situation, never the contract. Without it,
# a tool-holding session in an empty project answers as a repo agent ("there's
# no code here"), which E9's knowledge-QA prompt set does not intend.
E9_PROMPT = ("A colleague DMs you this on Slack. You have no repo or project "
             "context to consult, and none is needed — answer from general "
             "engineering knowledge, describing the likely or common behaviour "
             "directly rather than asking to see their system.\n\n%s")


# ---------------------------------------------------------------- smoke tests

def smoke():
    home, proj = provision("terse", "high")
    before = hits_count(home)
    txt, sid, err = session_call(
        "Repeat this sentence back to me exactly, as your whole reply: "
        "Great question! Furthermore, the retry budget is three attempts.",
        home, proj, "smoke-bounce")
    bounced = hits_count(home) > before
    texts = assistant_texts(home, proj, sid) if sid else []
    ok_a = bounced and txt is not None
    # (b) SessionStart injection fired: the skill's sessions.json records ids
    sess_file = os.path.join(home, ".claude", "skills", "speakingwords", "sessions.json")
    ok_b = os.path.isfile(sess_file) and sid is not None
    # (c) low rewrite returns prose, no refusal
    home2, proj2 = provision("convo", "low")
    fix = sorted(p9.e8_fixtures(MANIFEST))[0]
    ftext = open(fix, encoding="utf-8").read()
    txt2, sid2, err2 = session_call(E8_PROMPT % ftext, home2, proj2, "smoke-low")
    refusal = txt2 is None or "prompt injection" in (txt2 or "").lower() or len(txt2 or "") < 30
    ok_c = not refusal
    report = {
        "a_bounce": {"ok": ok_a, "hits_delta": hits_count(home) - before,
                     "assistant_turns": len(texts), "err": err},
        "b_injection": {"ok": ok_b, "sessions_file": os.path.isfile(sess_file)},
        "c_low_prose": {"ok": ok_c, "len": len(txt2 or ""), "err": err2,
                        "cut_pct": round(p9.reduction(ftext, txt2) * 100, 1) if txt2 else None},
    }
    json.dump(report, open(os.path.join(RIG, "smoke.json"), "w"), indent=1)
    print(json.dumps(report, indent=1))
    return all(v["ok"] for v in report.values())


# ------------------------------------------------------------------------- E8

def run_e8():
    paths = sorted(p9.e8_fixtures(MANIFEST))
    fixtures = {os.path.basename(p): open(p, encoding="utf-8").read() for p in paths}
    installs = {lvl: provision("convo", lvl) for lvl in p9.LEVELS}
    jobs = []
    for name, text in fixtures.items():
        for level in p9.LEVELS:
            home, proj = installs[level]
            jobs.append(((name, level),
                         (E8_PROMPT % text, home, proj, "%s|%s" % (name, level))))
    sys.stderr.write("E8: %d installed rewrites\n" % len(jobs))
    res = fanout(jobs, session_call)
    rewrites, errors = {}, []
    for (name, level), (txt, _sid, err) in res.items():
        if err:
            errors.append(err)
        if txt:
            rewrites["%s|%s" % (name, level)] = txt

    def rewriter(text, level):
        name = next(n for n, t in fixtures.items() if t == text)
        r = rewrites.get("%s|%s" % (name, level))
        if r is None:
            raise KeyError("missing rewrite")
        return r

    usable = [p for p in paths
              if all(rewrites.get("%s|%s" % (os.path.basename(p), l)) for l in p9.LEVELS)]
    medians = p9.e8_measure(usable, rewriter)
    bands = {l: p9.in_band(medians[l], l) for l in p9.LEVELS}
    per_pair = {}
    for p in usable:
        name = os.path.basename(p)
        for level in p9.LEVELS:
            per_pair["%s|%s" % (name, level)] = p9.reduction(
                fixtures[name], rewrites["%s|%s" % (name, level)])

    jjobs = [(k, (base.e8_judge_prompt(fixtures[k.split("|")[0]], rewrites[k]), k))
             for k in sorted(per_pair)]
    sys.stderr.write("\nE8: %d fact-loss judgements\n" % len(jjobs))
    jres = fanout(jjobs, bare_call)
    losses, judge_errors, judgements = [], [], {}
    for k, (txt, err) in jres.items():
        if err:
            judge_errors.append(err)
            continue
        obj = base.parse_json(txt)
        if obj is None:
            judge_errors.append("%s: unparseable judge output" % k)
            continue
        judgements[k] = obj
        if obj.get("loss"):
            losses.append({"pair": k, "reason": obj.get("reason", "")})

    gate_pairs, bullet_counts = [], {}
    for p in usable:
        name = os.path.basename(p)
        for level in p9.LEVELS:
            after = rewrites["%s|%s" % (name, level)]
            gate_pairs.append(("%s|%s" % (name, level), "convo", level,
                               fixtures[name], after))
            bullet_counts["%s|%s" % (name, level)] = [
                hc.bullet_line_count(fixtures[name]), hc.bullet_line_count(after)]
    a27 = hc.convo_prose_gate(gate_pairs)

    out = {
        "levels": list(p9.LEVELS),
        "bands": {l: list(p9.E8_BANDS[l]) for l in p9.LEVELS},
        "fixtures_usable": len(usable),
        "medians": medians,
        "in_band": bands,
        "per_pair_reduction": per_pair,
        "fact_loss_judgements": judgements,
        "losses": losses,
        "a27_convo_prose_gate": a27,
        "bullet_counts": bullet_counts,
        "rewrite_errors": errors,
        "judge_errors": judge_errors,
        "_rewrites": rewrites,
    }
    json.dump(out, open(os.path.join(RIG, "e8_raw.json"), "w"), indent=1)
    return out


# ------------------------------------------------------------------------- E9

def run_e9():
    prompts = MANIFEST["e9_prompts"]
    installs = {v: provision(v, E9_LEVEL) for v in p10.VOICES}
    jobs = []
    for i, pr in enumerate(prompts):
        for voice in p10.VOICES:
            home, proj = installs[voice]
            jobs.append(("p%02d|%s" % (i + 1, voice),
                         (E9_PROMPT % pr, home, proj, "p%02d|%s" % (i + 1, voice),
                          1, False)))
    sys.stderr.write("\nE9: %d installed replies (Stop hook live)\n" % len(jobs))
    res = fanout(jobs, session_call)

    first, final, bounced, errors = {}, {}, {}, []
    for key, (txt, sid, err) in res.items():
        if err:
            errors.append(err)
            continue
        voice = key.split("|")[1]
        home, proj = installs[voice]
        texts = assistant_texts(home, proj, sid) if sid else []
        first[key] = texts[0] if texts else txt
        final[key] = txt
        bounced[key] = len(texts) > 1

    def q_of(key):
        return prompts[int(key.split("|")[0][1:]) - 1]

    def judge(items, label):
        jjobs = []
        for key, question, reply in items:
            for axis in p10.E9_AXES:
                jjobs.append(((key, axis),
                              (base.e9_judge_prompt(axis, question, reply),
                               "%s/%s/%s" % (label, key, axis))))
        res2 = fanout(jjobs, bare_call)
        out, errs = {}, []
        for (key, axis), (txt, err) in res2.items():
            if err:
                errs.append(err)
                continue
            obj = base.parse_json(txt)
            if obj is None:
                errs.append("%s/%s/%s: unparseable" % (label, key, axis))
                continue
            out.setdefault(key, {})[axis] = obj
        return out, errs

    items = [(k, q_of(k), v) for k, v in sorted(first.items()) if v]
    sys.stderr.write("\nE9: %d first-reply judgements\n" % (len(items) * 2))
    judged, jerrs = judge(items, "first")

    first_scores = {}
    for k in sorted(first):
        if k not in judged or len(judged[k]) < 2:
            first_scores[k] = None
            continue
        first_scores[k] = {a: bool(judged[k][a].get("pass")) for a in p10.E9_AXES}
    scored = [v for v in first_scores.values() if v]
    first_rate = p10.e9_rate(scored)
    axis_rates = {a: (sum(1 for v in scored if v[a]) / float(len(scored)) if scored else 0.0)
                  for a in p10.E9_AXES}

    # Post-bounce: the PRODUCT's one bounce, not a simulated rewrite. A reply
    # the hook bounced gets its final output judged; a reply the hook let
    # through keeps its first-reply score — regex rules cannot see register.
    rejudge = [(k, q_of(k), final[k]) for k, v in sorted(first_scores.items())
               if v and not p10.e9_pass(v) and bounced.get(k) and final.get(k)]
    sys.stderr.write("\nE9: %d post-bounce re-judgements\n" % (len(rejudge) * 2))
    bjudged_raw, berrs = judge(rejudge, "post") if rejudge else ({}, [])
    bjudged = {}
    for k, axes in bjudged_raw.items():
        if len(axes) == 2:
            bjudged[k] = {a: bool(axes[a].get("pass")) for a in p10.E9_AXES}

    post = {}
    for k, v in first_scores.items():
        if not v:
            continue
        post[k] = v if p10.e9_pass(v) else bjudged.get(k, v)
    post_rate = p10.e9_rate(list(post.values()))

    out = {
        "level": E9_LEVEL,
        "prompts": len(prompts),
        "replies_attempted": len(jobs),
        "replies_ok": len(first),
        "hook_bounced": {k: bool(v) for k, v in bounced.items()},
        "bounce_count": sum(1 for v in bounced.values() if v),
        "first_scores": first_scores,
        "first_reasons": {k: {a: judged[k][a].get("reason", "") for a in judged[k]}
                          for k in judged},
        "first_rate": first_rate,
        "axis_rates": axis_rates,
        "first_gate": p10.E9_FIRST_REPLY_GATE,
        "post_gate": p10.E9_POST_BOUNCE_GATE,
        "post_scores": bjudged,
        "post_rate": post_rate,
        "reply_errors": errors,
        "judge_errors": jerrs + berrs,
        "_first": first,
        "_final": final,
    }
    json.dump(out, open(os.path.join(RIG, "e9_raw.json"), "w"), indent=1)
    return out


if __name__ == "__main__":
    os.makedirs(RIG, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which == "smoke":
        sys.exit(0 if smoke() else 1)
    if which in ("e8", "both"):
        run_e8()
    if which in ("e9", "both"):
        run_e9()
    sys.stderr.write("\ndone\n")
