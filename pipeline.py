"""
claimgate — a four-step agentic pipeline for looksmaxxing.guide

    ingest → classify → gate → draft → validate → route

Each arrow is a real handoff: a typed object, not a conversation. Agents do
judgement work (extraction, tiering, drafting). Code does enforcement.

Run:  python -m claimgate.pipeline --input data/claims.jsonl
"""

import argparse
import json
import os
import pathlib
import sys
from dataclasses import dataclass, asdict

from agents.gate import Tier, classify, validate_draft

try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
except Exception:
    _client = None

MODEL = os.environ.get("CLAIMGATE_MODEL", "gpt-4o-mini")
DRY = os.environ.get("CLAIMGATE_DRY", "0") == "1"


# ---------- handoff objects ----------

@dataclass
class Claim:
    source: str
    raw: str


@dataclass
class Extracted:
    claim: str
    practice: str
    outcome_promised: str


@dataclass
class Classified:
    extracted: Extracted
    agent_tier: int
    agent_confidence: float
    agent_rationale: str


@dataclass
class Routed:
    claim: str
    tier: int
    auto_publishable: bool
    gate_reasons: list
    gate_overrode_agent: bool
    draft: str
    violations: list
    destination: str


# ---------- model call ----------

def _ask(system: str, user: str, schema_hint: str) -> dict:
    if DRY or _client is None:
        return {"_dry": True}
    r = _client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system + "\nReturn JSON only. " + schema_hint},
            {"role": "user", "content": user},
        ],
    )
    return json.loads(r.choices[0].message.content)


# ---------- step 1: extract ----------

EXTRACT_SYS = """You normalise looksmaxxing claims scraped from forums and video
transcripts. Strip hype, keep the assertion. Do not evaluate whether it is true
or safe — that is a later step. If the text contains no testable claim about
appearance, return practice as "none"."""

def step_extract(c: Claim) -> Extracted:
    out = _ask(
        EXTRACT_SYS, c.raw,
        'Keys: claim (one sentence), practice (2-4 words), outcome_promised (short).',
    )
    if out.get("_dry"):
        return Extracted(claim=c.raw[:160], practice="unparsed", outcome_promised="unknown")
    return Extracted(
        claim=out.get("claim", "")[:400],
        practice=out.get("practice", "unknown"),
        outcome_promised=out.get("outcome_promised", "unknown"),
    )


# ---------- step 2: classify ----------

CLASSIFY_SYS = """Assign a risk tier to a looksmaxxing practice.

0 COSMETIC     reversible, non-invasive, no clinician needed (haircut, skincare, grooming)
1 BEHAVIOURAL  habit or training change (sleep, lifting, posture, diet quality)
2 CLINICAL     prescription drugs, dermatological or surgical procedures, anything
               requiring a licensed provider
3 DANGEROUS    self-injury, procedures performed by unlicensed people, unregulated
               compounds, or restriction practices

Err upward. A wrong tier-3 costs an editor five minutes. A wrong tier-0 puts
instructions for self-harm on a site teenagers read. Give your real confidence,
not a default high number."""

def step_classify(e: Extracted) -> Classified:
    out = _ask(
        CLASSIFY_SYS,
        f"Practice: {e.practice}\nClaim: {e.claim}\nPromised: {e.outcome_promised}",
        'Keys: tier (0-3 int), confidence (0-1 float), rationale (one sentence).',
    )
    if out.get("_dry"):
        return Classified(e, 1, 0.0, "dry run")
    return Classified(
        extracted=e,
        agent_tier=int(out.get("tier", 3)),
        agent_confidence=float(out.get("confidence", 0.0)),
        agent_rationale=out.get("rationale", ""),
    )


# ---------- step 3: draft ----------

DRAFT_SYS = """Write a short explainer for looksmaxxing.guide.

House rules, non-negotiable:
- Never state a dose, concentration, or quantity of any substance.
- Never describe how to perform a procedure.
- For tier 2, name the type of clinician who handles this and stop there.
- For tier 3, lead with the harm. Explain why people believe it works — usually
  survivorship bias — and why the retelling is more dangerous than the practice.
- No hype. The reader has been sold to enough already.
"""

def step_draft(c: Classified, tier: Tier) -> str:
    out = _ask(
        DRAFT_SYS,
        f"Tier: {int(tier)}\nPractice: {c.extracted.practice}\nClaim: {c.extracted.claim}",
        'Keys: draft (120-200 words).',
    )
    if out.get("_dry"):
        return f"[dry run] tier {int(tier)} explainer for {c.extracted.practice}. Risk of harm noted. Consult a qualified provider."
    return out.get("draft", "")


# ---------- orchestration ----------

def run_one(c: Claim) -> Routed:
    extracted = step_extract(c)
    classified = step_classify(extracted)

    # Gate sees the RAW text, not just what the agent reported. An agent that
    # sanitises a claim during extraction cannot smuggle it past the gate.
    gate = classify(c.raw + " " + extracted.claim, classified.agent_tier)

    draft = step_draft(classified, gate.tier)
    violations = validate_draft(draft, gate.tier)

    if violations:
        dest = "rejected:output-violation"
    elif gate.tier == Tier.DANGEROUS:
        dest = "queue:harm-reduction-desk"
    elif gate.tier == Tier.CLINICAL:
        dest = "queue:medical-editor"
    elif classified.agent_confidence < 0.6:
        dest = "queue:low-confidence"
    else:
        dest = "publish:auto"

    return Routed(
        claim=extracted.claim,
        tier=int(gate.tier),
        auto_publishable=gate.auto_publishable and not violations,
        gate_reasons=gate.reasons,
        gate_overrode_agent=gate.forced,
        draft=draft,
        violations=violations,
        destination=dest,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/claims.jsonl")
    ap.add_argument("--out", default="data/routed.jsonl")
    a = ap.parse_args()

    src = pathlib.Path(a.input)
    if not src.exists():
        sys.exit(f"no input at {src}")

    results = []
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        r = run_one(Claim(source=row.get("source", "unknown"), raw=row["raw"]))
        results.append(asdict(r))
        flag = " [GATE OVERRODE AGENT]" if r.gate_overrode_agent else ""
        print(f"tier {r.tier}  {r.destination:<28} {r.claim[:60]}{flag}")

    pathlib.Path(a.out).write_text("\n".join(json.dumps(x) for x in results))
    auto = sum(1 for x in results if x["auto_publishable"])
    over = sum(1 for x in results if x["gate_overrode_agent"])
    print(f"\n{len(results)} claims · {auto} auto-published · {over} gate overrides")


if __name__ == "__main__":
    main()
