#!/usr/bin/env python3
"""
generate_examples.py — Generates real-world behavior example cards for the
Evaluation Wizard from rubric.json, using the Anthropic API.

Usage:
    python3 generate_examples.py                              # all competencies
    python3 generate_examples.py --competency ownership-autonomy  # one competency
    python3 generate_examples.py --dry-run                    # preview prompts only

Output:
    examples.json  (same directory as this script)

Requirements:
    ANTHROPIC_API_KEY environment variable must be set.

Re-run whenever rubric.json is updated to refresh examples. Any guidance or
redirects from review sessions should be captured in GENERATION_NOTES below
so that a future re-run reproduces the same quality without repeating the
conversation.

──────────────────────────────────────────────────────────────────────────────
GENERATION NOTES  (updated after each review session)
──────────────────────────────────────────────────────────────────────────────

Session 1 — initial generation
  - Cards should be grounded in tech company contexts: PRs, incidents,
    design reviews, planning meetings, postmortems, oncall, mentoring
  - Use {{name}}, {{they}}, {{their}}, {{them}} as pronouns — substituted
    at render time by the wizard based on evaluator input
  - Level signal words must be embedded in behavior, not stated explicitly
  - Domain-Specific cards are placeholders: flagged placeholder=true,
    reviewed and refined after track content is finalized
  - Card action in UI: "More junior than me" / "Sounds like me" /
    "More senior than me" — cards should be specific enough to trigger
    a clear reaction but not so obvious the level is telegraphed
  - Scope: generate for all 6 levels per competency; wizard applies
    the +/- 1 window at runtime based on declared starting level

──────────────────────────────────────────────────────────────────────────────
"""

import json, os, sys, time, uuid, argparse, urllib.request, urllib.error
from pathlib import Path

BASE          = Path(__file__).parent
RUBRIC_PATH   = BASE / "rubric.json"
OUTPUT_PATH   = BASE / "examples.json"
MODEL         = "claude-opus-4-5"
CARDS_PER_LEVEL = 3   # number of example cards generated per competency × level

# ── Level signal vocabulary (mirrors philosophy §15) ─────────────────────────
LEVEL_SIGNALS = {
    "L1": "acting with guidance, task-scoped; the behavior is just beginning to form",
    "L2": "acting with minimal guidance, own-work-scoped; doing this regularly",
    "L3": "acting independently, feature/project-scoped; consistently delivering",
    "L4": "acting without being asked, team/cross-team-scoped; proactively",
    "L5": "setting standards, org-scoped; systematically improving how the team works",
    "L6": "defining the philosophy, company/industry-scoped; rare and sustained",
}

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are generating real-world behavior example cards for a performance \
evaluation wizard used at a tech company. Each card describes a concrete, observable \
situation that illustrates what a specific competency looks like at a specific level — \
written in the third person.

Use these exact placeholder tokens (they are substituted at render time):
  {{name}}  — the person being evaluated
  {{they}}  — subject pronoun  (she / he / they)
  {{their}} — possessive       (her / his / their)
  {{them}}  — object pronoun   (her / him / them)

VOICE AND TONE
Cards should read like something that actually happened, not a rubric definition. \
No jargon. No phrases like "demonstrates proficiency" or "exhibits behavior." \
Write like a specific human did a specific thing in a specific context at a tech company — \
think pull requests, incidents, planning meetings, cross-team projects, design reviews, \
onboarding, postmortems, oncall rotations, architecture discussions, mentoring sessions.

FORMAT
2–3 sentences per card. Situation + what the person did + (optionally) the outcome or signal.

WHAT MAKES A GOOD CARD
- Specific enough to be recognizable, general enough to apply across companies
- The behavior is observable — a manager could have witnessed it
- The level signal is embedded in the behavior, not stated — never say "as an L3 would"
- Grounded in real tech work, not abstract management language

LEVEL SIGNALS TO EMBED (do not state these explicitly in the card text):
  L1: beginning to develop habits, task-scoped, acting with guidance
  L2: regularly doing, own-work-scoped, minimal guidance needed
  L3: consistently delivering, feature/project-scoped, independently
  L4: proactively, team/cross-team-scoped, without being asked
  L5: systematically, org-scoped, setting the standard
  L6: defining the philosophy, company/industry-scoped, rare and sustained

OUTPUT FORMAT
Return a JSON array of exactly {n} card objects. No prose, no markdown — raw JSON only.
Each object: {{"scenario": "<2-3 sentence card text using {{name}} etc.>"}}
"""

# ── API call ──────────────────────────────────────────────────────────────────
def call_api(system: str, user: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["content"][0]["text"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode()}") from e


# ── User prompt builder ───────────────────────────────────────────────────────
def build_user_prompt(competency: dict, level: str, capabilities: list,
                      is_placeholder: bool) -> str:
    cap_context = "\n".join(
        f"  - {c['name']}: {c.get('intent', '')}" for c in capabilities
    )
    placeholder_note = (
        "\nNOTE: This is a placeholder pass for a domain-specific competency. "
        "Generate plausible generic tech examples that will be refined later "
        "once track-specific content is finalized. Flag this by including the "
        "word PLACEHOLDER in a comment — do not include it in the scenario text.\n"
        if is_placeholder else ""
    )

    return f"""Generate {CARDS_PER_LEVEL} example cards for:

COMPETENCY: {competency['name']}
Competency intent: {competency.get('intent', '')}

CAPABILITIES IN THIS COMPETENCY (use as context, do not reference directly):
{cap_context}

TARGET LEVEL: {level}
Level signal to embed: {LEVEL_SIGNALS[level]}
{placeholder_note}
Return a JSON array of {CARDS_PER_LEVEL} objects: [{{"scenario": "..."}}]
"""


# ── Parse API response ────────────────────────────────────────────────────────
def parse_cards(raw: str, competency_id: str, level: str,
                track: str | None, is_placeholder: bool) -> list[dict]:
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        # Attempt to extract first JSON array from response
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            items = json.loads(match.group())
        else:
            print(f"    ⚠ Could not parse response for {competency_id}/{level}:")
            print(f"    {raw[:200]}")
            return []

    return [
        {
            "id":            str(uuid.uuid4()),
            "competency_id": competency_id,
            "level":         level,
            "track":         track,
            "placeholder":   is_placeholder,
            "scenario":      item["scenario"],
        }
        for item in items
        if isinstance(item, dict) and "scenario" in item
    ]


# ── Main generation logic ─────────────────────────────────────────────────────
def collect_competencies(rubric: dict) -> list[dict]:
    """
    Returns a flat list of competency specs to generate:
      { competency, capabilities, track, is_placeholder }
    Domain-Specific gets one entry per available track.
    """
    specs = []
    available_tracks = [t["id"] for t in rubric["meta"]["tracks"] if t.get("available")]

    for dim in rubric["dimensions"]:
        for comp in dim["competencies"]:
            if comp["id"] == "domain-specific":
                # One entry per track, flagged as placeholder
                for track in available_tracks:
                    # Resolve track-specific capabilities if present
                    caps = []
                    for cap in comp["capabilities"]:
                        if cap.get("track_specific") and isinstance(
                            list(cap["descriptions"].values())[0], dict
                        ):
                            descs = cap["descriptions"].get(track, {})
                        else:
                            descs = cap.get("descriptions", {})
                        caps.append({**cap, "descriptions": descs})
                    specs.append({
                        "competency":   comp,
                        "capabilities": caps,
                        "track":        track,
                        "is_placeholder": True,
                    })
            else:
                specs.append({
                    "competency":   comp,
                    "capabilities": comp["capabilities"],
                    "track":        None,
                    "is_placeholder": False,
                })
    return specs


def generate(args):
    rubric = json.load(open(RUBRIC_PATH, encoding="utf-8"))
    levels = rubric["meta"]["levels"]  # ['L1', 'L2', 'L3', 'L4', 'L5', 'L6']

    # Load existing examples to support incremental re-runs
    if OUTPUT_PATH.exists():
        existing = json.load(open(OUTPUT_PATH, encoding="utf-8"))
        existing_keys = {
            (e["competency_id"], e["level"], e["track"]) for e in existing
        }
    else:
        existing, existing_keys = [], set()

    specs = collect_competencies(rubric)

    # Filter to a single competency if requested
    if args.competency:
        specs = [s for s in specs if s["competency"]["id"] == args.competency]
        if not specs:
            print(f"Competency '{args.competency}' not found.")
            print("Available:", [s["competency"]["id"] for s in collect_competencies(rubric)])
            sys.exit(1)

    system = SYSTEM_PROMPT.replace("{n}", str(CARDS_PER_LEVEL))
    new_cards = []

    for spec in specs:
        comp        = spec["competency"]
        caps        = spec["capabilities"]
        track       = spec["track"]
        placeholder = spec["is_placeholder"]
        label       = f"{comp['id']} [{track or 'shared'}]"

        for level in levels:
            key = (comp["id"], level, track)
            if key in existing_keys:
                print(f"  ↷ skip  {label} / {level}  (already generated)")
                continue

            if args.dry_run:
                print(f"  ○ would generate  {label} / {level}")
                print(build_user_prompt(comp, level, caps, placeholder)[:300])
                print()
                continue

            print(f"  ⟳ generating  {label} / {level} ...", end=" ", flush=True)
            user_prompt = build_user_prompt(comp, level, caps, placeholder)

            try:
                raw   = call_api(system, user_prompt)
                cards = parse_cards(raw, comp["id"], level, track, placeholder)
                new_cards.extend(cards)
                print(f"✓ {len(cards)} cards")
            except Exception as e:
                print(f"✗ {e}")

            time.sleep(0.5)  # gentle rate limiting

    if not args.dry_run:
        all_cards = existing + new_cards
        OUTPUT_PATH.write_text(
            json.dumps(all_cards, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"\n✓ Written: {OUTPUT_PATH.name}  ({len(all_cards)} total cards, {len(new_cards)} new)")
    else:
        print(f"\n(dry-run complete — {OUTPUT_PATH.name} not written)")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate evaluation wizard example cards")
    parser.add_argument("--competency", help="Generate for a single competency ID only")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview prompts without calling the API")
    args = parser.parse_args()
    generate(args)
