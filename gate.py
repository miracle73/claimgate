"""
Deterministic safety gate.

This runs OUTSIDE the model on purpose. A prompt is a request; a check is a
guarantee. Everything in here is hard-coded policy that no agent output can
talk its way past, regardless of how confident the classifier was.
"""

import re
from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    COSMETIC = 0      # skincare, grooming, haircuts — auto-publish
    BEHAVIOURAL = 1   # sleep, training, posture — auto-publish
    CLINICAL = 2      # anything prescription, dermatological, surgical — human review
    DANGEROUS = 3     # self-injury, unlicensed procedures, unregulated compounds — never auto


# Practices that are self-injury or criminal when done outside a clinic.
# Presence of any of these forces Tier.DANGEROUS no matter what the agent said.
HARD_BLOCK = [
    r"\bbone\s?smash", r"\bmewing\s+with\s+(force|weights)", r"\bhammer\b.*\bjaw",
    r"\bchisel", r"\bself[-\s]?inject", r"\baqualyx", r"\bhome\s+filler",
    r"\bDIY\s+(filler|botox|peel|surgery)", r"\bcarpal\s+hammer",
    r"\bstarv", r"\bpurg", r"\bdry\s?fast(ing)?\b", r"\bwater[-\s]?cut\b",
]

# Regulated / prescription territory. Never auto-publishes, but is publishable
# with an editor in the loop — this is the site's actual bread and butter.
CLINICAL_MARKERS = [
    r"\bfinasteride\b", r"\bminoxidil\b", r"\bdutasteride\b", r"\baccutane\b",
    r"\bisotretinoin\b", r"\btretinoin\b", r"\bGLP-?1\b", r"\bozempic\b",
    r"\bsemaglutide\b", r"\btirzepatide\b", r"\bTRT\b", r"\btestosterone\b",
    r"\bpeptide\b", r"\bmelanotan\b", r"\bSARM", r"\bsteroid", r"\bfiller\b",
    r"\bbotox\b", r"\bsurgery\b", r"\bimplant\b", r"\brhinoplasty\b",
]

# Output-level bans. These apply to DRAFTS, not claims. Even a correctly
# tiered clinical piece must not ship a dose or a how-to.
DOSE_PATTERN = re.compile(
    r"\b\d+(\.\d+)?\s?(mg|mcg|ml|iu|cc|units?)\b(?!\s*(of\s+)?(caffeine|protein|water))",
    re.I,
)
PROCEDURE_HOWTO = re.compile(
    r"\b(inject|apply\s+the\s+needle|insert\s+the|incise|numb\s+the\s+area|"
    r"draw\s+up\s+the)\b", re.I,
)


@dataclass
class GateResult:
    tier: Tier
    auto_publishable: bool
    reasons: list
    forced: bool  # True if the gate overrode the agent's classification


def _hits(patterns, text):
    return [p for p in patterns if re.search(p, text, re.I)]


def classify(text: str, agent_tier: int) -> GateResult:
    """
    Takes the claim text and whatever tier the classifier agent proposed.
    The gate can only ever RAISE severity, never lower it. An agent that
    decides bonesmashing is 'behavioural' gets overruled; an agent that is
    excessively cautious is left alone.
    """
    reasons, forced = [], False
    tier = Tier(max(0, min(3, int(agent_tier))))

    blocked = _hits(HARD_BLOCK, text)
    if blocked:
        if tier < Tier.DANGEROUS:
            forced = True
            reasons.append(
                f"gate override: agent said tier {int(tier)}, hard-block matched {blocked}"
            )
        tier = Tier.DANGEROUS
        reasons.append(f"hard-block patterns: {blocked}")

    clinical = _hits(CLINICAL_MARKERS, text)
    if clinical and tier < Tier.CLINICAL:
        forced = True
        tier = Tier.CLINICAL
        reasons.append(f"gate override: clinical markers present {clinical}")

    auto = tier <= Tier.BEHAVIOURAL
    if not auto:
        reasons.append(
            "routed to human editor" if tier == Tier.CLINICAL
            else "never auto-publishes; harm-reduction framing required"
        )
    return GateResult(tier=tier, auto_publishable=auto, reasons=reasons, forced=forced)


def validate_draft(draft: str, tier: Tier) -> list:
    """
    Output-side checks. Run on the generated draft, after the claim has
    already been tiered. Returns a list of violations; empty means it passes.
    """
    v = []
    if DOSE_PATTERN.search(draft):
        v.append(f"contains a dose: {DOSE_PATTERN.search(draft).group(0)!r}")
    if PROCEDURE_HOWTO.search(draft):
        v.append(f"contains procedural instruction: {PROCEDURE_HOWTO.search(draft).group(0)!r}")
    if tier >= Tier.CLINICAL and "consult" not in draft.lower():
        v.append("clinical-tier draft with no referral to a qualified provider")
    if tier == Tier.DANGEROUS and not re.search(r"\b(harm|risk|danger|injur)", draft, re.I):
        v.append("dangerous-tier draft that does not state the harm")
    return v
