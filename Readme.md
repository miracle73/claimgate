# claimgate

A four-step agentic pipeline that triages looksmaxxing claims before they
become published guides.

    ingest → extract → classify → [deterministic gate] → draft → validate → route

## Why this shape

looksmaxxing.guide's editorial value is harm reduction. Its strongest pages
are the ones explaining why bonesmashing and unlicensed Aqualyx injections are
dangerous, not the ones telling you to moisturise. So the pipeline's job is not
"generate content faster". It is: separate the claims that can publish
themselves from the ones that must never touch an automated path.

## The design decision that matters

Steps 1, 2 and 4 are agents. Step 3 is not, and never will be.

The classifier agent proposes a risk tier. A hard-coded gate (`agents/gate.py`)
can **raise** that tier but never lower it. Bonesmashing classified as
"behavioural" gets forced to tier 3 by regex, and the override is logged. A
prompt is a request. A check is a guarantee.

The gate also reads the **raw** source text, not the agent's cleaned-up
extraction — otherwise an extraction agent that sanitises phrasing can smuggle
a dangerous claim past a gate that only sees its own output. That was a real
bug, found by feeding it the Aqualyx case.

## Tiers

| tier | meaning | destination |
|---|---|---|
| 0 cosmetic | reversible, no clinician | auto-publish |
| 1 behavioural | habit or training change | auto-publish |
| 2 clinical | prescription, derm, surgical | medical editor queue |
| 3 dangerous | self-injury, unlicensed, unregulated | harm-reduction desk, never auto |

Output-side validation runs separately from claim tiering: even a correctly
tiered clinical draft is rejected if it contains a dose or a procedure how-to.

## Run

    export OPENAI_API_KEY=...
    python pipeline.py --input data/claims.jsonl

    CLAIMGATE_DRY=1 python pipeline.py   # structure only, no model calls

## Sample output

    tier 0  publish:auto                 double cleanse at night improved skin texture
    tier 3  queue:harm-reduction-desk    bonesmashing with a hammer [GATE OVERRODE AGENT]
    tier 2  queue:medical-editor         finasteride 1mg daily stabilised hairline
    tier 1  publish:auto                 8 hours sleep improved under-eyes
    tier 3  queue:harm-reduction-desk    aqualyx injected by an unlicensed person
    tier 3  queue:harm-reduction-desk    dry fasting before a photoshoot
    tier 1  publish:auto                 mewing tongue posture over 2 years
