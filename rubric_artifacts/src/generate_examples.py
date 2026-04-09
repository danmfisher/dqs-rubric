#!/usr/bin/env python3
"""
generate_examples.py — Generates real-world behavior example cards for the
Evaluation Wizard from rubric.json, using the Anthropic or OpenAI API.

Usage:
    python3 generate_examples.py                              # all competencies
    python3 generate_examples.py --competency ownership-autonomy  # one competency
    python3 generate_examples.py --dry-run                    # preview prompts only

Output:
    examples.json  (same directory as this script)

Requirements:
    Set the corresponding environment variable and pass the provider flag:
      --anthropic  uses ANTHROPIC_API_KEY  with claude-opus-4-5
      --openai     uses OPENAI_API_KEY     with gpt-4o
    Defaults to --anthropic if neither flag is passed.

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
  - Scope: generate for all 6 levels per competency; wizard applies
    the +/- 1 window at runtime based on declared starting level

Session 2 — card action and framing revision
  - Card action in UI changed to: "Easy" / "Comfortable" / "Stretch"
    This replaces the earlier "More junior / Sounds like me / More senior"
    framing, which didn't account for the "I do that AND more" dynamic
    of level progression. An L4 should be able to mark L2 behaviors as
    "Easy" — that's useful signal, not noise.
  - Easy     → below where they reliably operate; internalized
  - Comfortable → their home base; the level they're at
  - Stretch  → above where they reliably operate; aspirational
  - Triangulation logic: highest level where "Comfortable" is dominant
    response is the inferred level. Consecutive "Easy" responses shift
    the window up; "Stretch" responses shift it down.
  - Cards should capture the TEXTURE of effort, not just the behavior:
    the difference between an L2 and an L4 fixing a bug isn't what they
    did, it's that the L2 spent a day on it with one check-in while the
    L4 did it in twenty minutes before a meeting. Write cards that make
    the evaluator feel the weight of the task for the person described.
  - Cards no longer need to be maximally ambiguous — the classification
    (easy/comfortable/stretch) does the discriminating work. Cards should
    be clear and recognizable; the evaluator's reaction to them is the signal.
  - Avoid ending cards with evaluative summary sentences ("demonstrating
    their ability to...", "showing initiative in...") — show, don't tell.
  - Avoid specific metrics ("reduced time by 30%") — qualitative impact
    descriptions are more recognizable and less fabricated-feeling.

Session 3 — post-review fixes and prompt hardening
  - {{Name}} capitalization bug: model occasionally starts a mid-sentence
    reference with {{Name}} (capital N) instead of {{name}}. Fixed by
    post-processing regex in parse_cards — no prompt change needed.
  - Arc word leakage: "systematically" (L5 signal) appeared in L2 cards;
    "proactively" (L4 signal) leaked into L3 narrative text. Prompt now
    includes an explicit FORBIDDEN WORDS table per level.
  - L6 theme clichés: sustainability/green tech, data privacy, and
    diversity/inclusion were overused as default L6 themes across multiple
    competencies. Prompt now explicitly calls these out as clichés to avoid.
  - Duplicate themes at adjacent levels: team-multiplier L5 and L6 both
    generated onboarding-redesign scenarios. Prompt now instructs that
    cards within the same competency must use distinct concrete scenarios —
    no two cards should be about the same situation.
  - delivery-execution L6 card 3 used "sustainable technology / green
    objectives" framing — another instance of the L6 cliché pattern.
  - problem-structuring L4 card 1 used "50% reduction in incident
    frequency" — specific invented metric. Already covered by Session 2
    guidance but reinforced in FORBIDDEN PATTERNS.

──────────────────────────────────────────────────────────────────────────────
"""

import json, os, re, sys, time, uuid, argparse, urllib.request, urllib.error
from pathlib import Path

BASE          = Path(__file__).parent          # rubric_artifacts/src/
ARTIFACTS     = BASE.parent                    # rubric_artifacts/
RUBRIC_PATH   = ARTIFACTS / "rubric.json"
OUTPUT_PATH   = ARTIFACTS / "examples.json"
ANTHROPIC_MODEL = "claude-opus-4-5"
OPENAI_MODEL    = "gpt-4o"
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
- Captures the TEXTURE of effort: an L2 spending a day on something with one
  check-in reads differently than an L4 handling the same thing before a meeting.
  The weight and context of the task should be feelable, not just the action.
- Do NOT end cards with evaluative summary sentences ("demonstrating their ability
  to...", "showing initiative in...") — show what happened, let the reader judge.
- Do NOT use specific metrics ("reduced time by 30%") — qualitative impact reads
  as more authentic and recognizable than invented numbers.

LEVEL SIGNALS TO EMBED (do not state these explicitly in the card text):
These signal words are reserved — each word belongs to exactly one level.
Using "proactively" in an L2 card, or "regularly" in an L4 card, is an error.

  Signal      | L1              | L2                   | L3             | L4                  | L5             | L6
  ------------|-----------------|----------------------|----------------|---------------------|----------------|--------------------
  Frequency   | beginning to    | regularly            | consistently   | proactively         | systematically | defines the standard
  Agency      | with guidance   | with minimal guidance| independently  | without prompting   | without direction | sets the philosophy
  Scope       | task            | own work             | feature/project| team / cross-team   | org-wide       | company / industry

Embed the signal through the texture of the scenario — the size of the problem,
how much direction the person needed, and whether they were asked or self-directed.

FORBIDDEN WORDS PER LEVEL (using these in the wrong level is an error):
  L1 cards must NOT use: regularly, consistently, proactively, systematically, independently
  L2 cards must NOT use: proactively, systematically, consistently, without prompting, without direction
  L3 cards must NOT use: proactively, systematically, without prompting, without direction
  L4 cards must NOT use: systematically, without direction, beginning to, with guidance
  L5 cards must NOT use: beginning to, with guidance, with minimal guidance, proactively
  L6 cards must NOT use: beginning to, with guidance, with minimal guidance, regularly

FORBIDDEN PATTERNS (these are errors regardless of level):
  - Specific invented metrics: "reduced time by 30%", "50% reduction", "2x faster"
    Use qualitative impact instead: "significantly reduced", "noticeably faster"
  - Evaluative summary endings: "demonstrating their ability to...", "showing initiative in...",
    "highlighting their commitment to..." — end on what happened, not a judgment of it
  - Placeholder token capitalization: always {{name}}, {{they}}, {{their}}, {{them}} —
    never {{Name}}, {{They}}, {{Their}}, {{Them}}

L6 THEME VARIETY — avoid these overused defaults:
  The following themes have been used too many times across the L6 card set.
  Do not use them unless the competency makes them genuinely unavoidable:
    ✗ Sustainability / green technology / environmental impact
    ✗ Data privacy / GDPR / privacy-first frameworks
    ✗ Diversity, equity & inclusion initiatives
  Strong L6 cards are about engineering philosophy, technical architecture,
  industry-defining technical approaches, or company-wide process transformation —
  things that would be cited in engineering blog posts or conference talks about
  how to build excellent software organizations.

SCENARIO VARIETY — cards within the same competency must use distinct situations:
  Do not use the same concrete scenario (e.g., "redesigning the onboarding process")
  at two different levels within the same competency. Each card should describe a
  different type of situation, even if both sit at adjacent levels.

OUTPUT FORMAT
Return a JSON array of exactly {n} card objects. No prose, no markdown — raw JSON only.
Each object: {{"scenario": "<2-3 sentence card text using {{name}} etc.>"}}
"""

# ── API call ──────────────────────────────────────────────────────────────────
def call_api(system: str, user: str, provider: str) -> str:
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        return _call_openai(system, user, api_key)
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")
        return _call_anthropic(system, user, api_key)


def _call_anthropic(system: str, user: str, api_key: str) -> str:
    payload = json.dumps({
        "model":      ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "system":     system,
        "messages":   [{"role": "user", "content": user}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["content"][0]["text"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API error {e.code}: {e.read().decode()}") from e


def _call_openai(system: str, user: str, api_key: str) -> str:
    payload = json.dumps({
        "model":      OPENAI_MODEL,
        "max_tokens": 1024,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenAI API error {e.code}: {e.read().decode()}") from e


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
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            items = json.loads(match.group())
        else:
            print(f"    ⚠ Could not parse response for {competency_id}/{level}:")
            print(f"    {raw[:200]}")
            return []

    cards = []
    for item in items:
        if not isinstance(item, dict) or "scenario" not in item:
            continue
        scenario = item["scenario"]
        # Normalize {{Name}} → {{name}} (model occasionally capitalizes mid-sentence)
        scenario = re.sub(r'\{\{Name\}\}', '{{name}}', scenario)
        scenario = re.sub(r'\{\{They\}\}',  '{{they}}',  scenario)
        scenario = re.sub(r'\{\{Their\}\}', '{{their}}', scenario)
        scenario = re.sub(r'\{\{Them\}\}',  '{{them}}',  scenario)
        cards.append({
            "id":            str(uuid.uuid4()),
            "competency_id": competency_id,
            "level":         level,
            "track":         track,
            "placeholder":   is_placeholder,
            "scenario":      scenario,
        })
    return cards


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

    # Filter to a single level if requested
    if args.level:
        if args.level not in levels:
            print(f"Level '{args.level}' not found. Available: {levels}")
            sys.exit(1)
        levels = [args.level]

    # --force: drop matching existing cards so they get regenerated
    if args.force:
        force_keys = set()
        for spec in specs:
            for level in levels:
                force_keys.add((spec["competency"]["id"], level, spec["track"]))
        existing = [e for e in existing if (e["competency_id"], e["level"], e["track"]) not in force_keys]
        existing_keys -= force_keys
        print(f"  ⚠ --force: dropping {len(force_keys)} existing card set(s) for regeneration")

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
                raw   = call_api(system, user_prompt, args.provider)
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
    parser.add_argument("--level", help="Generate for a single level only (e.g. L4). Use with --competency.")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if cards already exist (drops and replaces matching entries)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview prompts without calling the API")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--anthropic", dest="provider", action="store_const", const="anthropic",
                       help="Use Anthropic API with ANTHROPIC_API_KEY (default)")
    group.add_argument("--openai", dest="provider", action="store_const", const="openai",
                       help="Use OpenAI API with OPENAI_API_KEY")
    parser.set_defaults(provider="anthropic")
    args = parser.parse_args()
    generate(args)
