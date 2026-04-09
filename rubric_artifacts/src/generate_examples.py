#!/usr/bin/env python3
"""
generate_examples.py — Generates real-world behavior example cards for the
Evaluation Wizard from rubric.json, using the Anthropic or OpenAI API.

Usage:
    python3 generate_examples.py                                   # all competencies, all levels
    python3 generate_examples.py --competency ownership-autonomy   # one competency
    python3 generate_examples.py --competency ownership-autonomy --level L4
    python3 generate_examples.py --force                           # regenerate existing cards
    python3 generate_examples.py --dry-run                         # preview prompts, no API calls

Output:
    rubric_artifacts/examples.json

Requirements:
    Set ANTHROPIC_API_KEY (default) or OPENAI_API_KEY and pass --openai.

Re-run whenever rubric.json is updated. Incremental by default — already-generated
(competency, level, track) triples are skipped unless --force is passed.

Any guidance or redirects from review sessions should be captured in
GENERATION_NOTES below so that a future re-run reproduces the same quality
without repeating the conversation.

──────────────────────────────────────────────────────────────────────────────
GENERATION NOTES  (updated after each review session)
──────────────────────────────────────────────────────────────────────────────

Session 1 — initial generation
  - Cards grounded in tech company contexts: PRs, incidents, design reviews,
    planning meetings, postmortems, oncall, mentoring
  - Use {{name}}, {{they}}, {{their}}, {{them}} — substituted at render time
  - Level signal words embedded in behavior, not stated explicitly
  - Domain-Specific cards are placeholders (placeholder=true), to be refined
    once track-specific content is finalized
  - Scope: all 6 levels per competency; wizard applies ±1 window at runtime

Session 2 — card action framing revision
  - Ratings changed to: Easy / Comfortable / Stretch (replaces More junior /
    Sounds like me / More senior — old framing didn't capture the "I do that
    AND more" dynamic of level progression)
  - Easy = internalized, below current operating level
  - Comfortable = home base
  - Stretch = aspirational, above current operating level
  - Cards should capture TEXTURE of effort, not just behavior — the difference
    between L2 and L4 on the same task is weight and context, not just action
  - Avoid evaluative endings ("demonstrating their ability to...") — show, don't tell
  - Avoid specific metrics ("reduced time by 30%") — qualitative reads more authentic

Session 3 — post-review prompt hardening
  - {{Name}} capitalization: post-processing regex in parse_cards normalizes
    {{Name}}/{{They}}/{{Their}}/{{Them}} → lowercase variants
  - Arc word leakage: level signal words appearing at wrong levels. Added
    explicit FORBIDDEN WORDS table to prompt.
  - L6 theme clichés: sustainability, data privacy, DEI overused as defaults.
    Added L6 THEME VARIETY section to prompt.
  - Duplicate scenarios at adjacent levels: added SCENARIO VARIETY section.

Session 4 — bold markers
  - BOLD MARKERS added to SYSTEM_PROMPT: one bold per sentence, three
    signal types must be represented across the card.
  - Key mental model: bold phrases have "direct lineage from the rubric."
    They are not general praise — they surface the exact vocabulary of the
    LEVEL SIGNALS table: frequency words (consistently, proactively, etc.),
    agency phrases (without waiting for specific direction, with minimal
    guidance, without prompting), and scope phrases (cross-team, org-wide).
  - Frequency word MUST be inside the bold span, not left outside it.
  - Agency phrase must be bolded in full — the whole phrase carries the
    signal ("without waiting for specific direction"), not just the verb.
  - Scope is most critical at L4+ where scale is the primary differentiator.
  - Bold is part of the default generation flow. Existing cards were
    retrofitted in a one-time pass; the standalone --add-bold flag removed.

──────────────────────────────────────────────────────────────────────────────
"""

import json
import os
import re
import sys
import time
import uuid
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# ── Paths and constants ───────────────────────────────────────────────────────

BASE          = Path(__file__).parent   # rubric_artifacts/src/
ARTIFACTS     = BASE.parent             # rubric_artifacts/
RUBRIC_PATH   = ARTIFACTS / "rubric.json"
OUTPUT_PATH   = ARTIFACTS / "examples.json"

ANTHROPIC_MODEL = "claude-opus-4-5"
OPENAI_MODEL    = "gpt-4o"
CARDS_PER_LEVEL = 3

LEVEL_SIGNALS = {
    "L1": "acting with guidance, task-scoped; the behavior is just beginning to form",
    "L2": "acting with minimal guidance, own-work-scoped; doing this regularly",
    "L3": "acting independently, feature/project-scoped; consistently delivering",
    "L4": "acting without being asked, team/cross-team-scoped; proactively",
    "L5": "setting standards, org-scoped; systematically improving how the team works",
    "L6": "defining the philosophy, company/industry-scoped; rare and sustained",
}

# ── Prompts ───────────────────────────────────────────────────────────────────

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

BOLD MARKERS
Bold is how the reader recognizes the level. Every bolded phrase must have direct lineage
from the LEVEL SIGNALS table — it should surface one of the three rubric signal dimensions.
Bolded phrases are not general praise; they are the specific words that tell a calibrated
evaluator what level they are looking at.

Every sentence must contain at least one bolded phrase. Across the card, the bolded phrases
must collectively surface all three signal types:

  1. ACTION — the verb phrase for what the person did. The sentence's frequency signal word
     (consistently, proactively, regularly, etc.) MUST be inside the bold span.
       ✓ "**consistently drove** the refactoring..."
       ✗ "consistently **drove** the refactoring..."  ← frequency word left outside

  2. AGENCY — the phrase that shows how self-directed they were. These come directly from
     the agency column of the LEVEL SIGNALS table. Bold the whole agency phrase:
       L1: "**with guidance**", "**with help from their manager**"
       L2: "**with minimal guidance**", "**starting with minimal input**"
       L3: "**independently**", "**without waiting for specific direction**"
       L4: "**without prompting**", "**without being asked**", "**on their own initiative**"
       L5: "**without direction**", "**setting the direction for the team**"
     The agency phrase must be bolded in full, whether it opens the sentence or is embedded mid-sentence.
       ✓ "**Without waiting for specific direction**, {{name}} refactored the module."
       ✓ "{{name}} refactored the module **without waiting for specific direction**."
       ✗ "without waiting for **specific direction**"  ← partial — the signal is in the full phrase

  3. SCOPE — the scale of impact, from the scope column of the LEVEL SIGNALS table.
     Scope is most critical at L4+, where it is the primary level differentiator.
       L3: "**across the feature**", "**for the project**"
       L4: "**across the team**", "**cross-team**", "**two teams**"
       L5: "**org-wide**", "**across the org**"
       L6: "**company-wide**", "**across the industry**"

In a 3-sentence card, aim for one signal type per sentence. In a 2-sentence card,
combine agency and scope into the same bold span when both are present.

Additional rules:
  - Bold MUST include the main verb — never bold a noun phrase or object alone
  - Do not bold outcomes or domains — bold the behavior, not what it produced

  ✓ "{{name}} **consistently drove the refactoring effort**."             ← action (freq inside)
     "**Without waiting for specific direction**, she caught the gaps."   ← agency (full phrase)
     "The fix **rolled out across three downstream teams**."              ← scope

  ✗ "{{name}} **consistently drove the refactoring effort**,
     without waiting for specific direction across three downstream teams."
     → agency phrase and scope left unbolded — only action represented

  ✓ "{{name}} **proactively coordinated a cross-team effort** to resolve the outage."
     (combines action + agency + scope in one span when card is tightly written)

  ✗ "{{name}} proactively coordinated **a cross-team effort** to resolve the outage."
     → "proactively" (the agency signal) left outside the bold span

OUTPUT FORMAT
Return a JSON array of exactly {n} card objects. No prose, no markdown — raw JSON only.
Each object: {{"scenario": "<2-3 sentence card text with **bold** markers on key behaviors>"}}
"""

# ── API layer ─────────────────────────────────────────────────────────────────

def call_api(system: str, user: str, provider: str, max_tokens: int = 1024) -> str:
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        return _call_openai(system, user, api_key, max_tokens)
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")
        return _call_anthropic(system, user, api_key, max_tokens)


def _call_anthropic(system: str, user: str, api_key: str, max_tokens: int) -> str:
    payload = json.dumps({
        "model":      ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
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


def _call_openai(system: str, user: str, api_key: str, max_tokens: int) -> str:
    payload = json.dumps({
        "model":      OPENAI_MODEL,
        "max_tokens": max_tokens,
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

# ── Prompt builder ────────────────────────────────────────────────────────────

def build_user_prompt(competency: dict, level: str, capabilities: list,
                      is_placeholder: bool) -> str:
    cap_lines = "\n".join(
        f"  - {c['name']}: {c.get('intent', '')}" for c in capabilities
    )
    placeholder_note = (
        "\nNOTE: This is a placeholder pass for a domain-specific competency. "
        "Generate plausible generic tech examples that will be refined later "
        "once track-specific content is finalized. Flag this by including the "
        "word PLACEHOLDER in a comment — do not include it in the scenario text.\n"
        if is_placeholder else ""
    )
    return (
        f"Generate {CARDS_PER_LEVEL} example cards for:\n\n"
        f"COMPETENCY: {competency['name']}\n"
        f"Competency intent: {competency.get('intent', '')}\n\n"
        f"CAPABILITIES IN THIS COMPETENCY (use as context, do not reference directly):\n"
        f"{cap_lines}\n\n"
        f"TARGET LEVEL: {level}\n"
        f"Level signal to embed: {LEVEL_SIGNALS[level]}\n"
        f"{placeholder_note}"
        f"Return a JSON array of {CARDS_PER_LEVEL} objects: [{{\"scenario\": \"...\"}}]\n"
    )

# ── Parser ────────────────────────────────────────────────────────────────────

def parse_cards(raw: str, competency_id: str, level: str,
                track: Optional[str], is_placeholder: bool) -> list:
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            items = json.loads(match.group())
        else:
            print(f"    ⚠ Could not parse response for {competency_id}/{level}: {raw[:200]}")
            return []

    cards = []
    for item in items:
        if not isinstance(item, dict) or "scenario" not in item:
            continue
        scenario = item["scenario"]
        # Normalize token capitalization (model occasionally capitalizes mid-sentence)
        scenario = re.sub(r'\{\{Name\}\}',  '{{name}}',  scenario)
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

# ── Data helpers ──────────────────────────────────────────────────────────────

def load_existing() -> tuple:
    """Returns (cards, existing_keys) from examples.json, or ([], set()) if absent."""
    if not OUTPUT_PATH.exists():
        return [], set()
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        cards = json.load(f)
    keys = {(c["competency_id"], c["level"], c["track"]) for c in cards}
    return cards, keys


def save_cards(cards: list) -> None:
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2, ensure_ascii=False)

# ── Competency collection ─────────────────────────────────────────────────────

def collect_specs(rubric: dict) -> list:
    """
    Returns a flat list of generation specs:
      { competency, capabilities, track, is_placeholder }
    Domain-Specific gets one entry per available track.
    """
    specs = []
    available_tracks = [t["id"] for t in rubric["meta"]["tracks"] if t.get("available")]

    for dim in rubric["dimensions"]:
        for comp in dim["competencies"]:
            if comp["id"] == "domain-specific":
                for track in available_tracks:
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
                        "competency":     comp,
                        "capabilities":   caps,
                        "track":          track,
                        "is_placeholder": True,
                    })
            else:
                specs.append({
                    "competency":     comp,
                    "capabilities":   comp["capabilities"],
                    "track":          None,
                    "is_placeholder": False,
                })
    return specs

# ── Main logic ────────────────────────────────────────────────────────────────

def generate(args):
    with open(RUBRIC_PATH, encoding="utf-8") as f:
        rubric = json.load(f)
    levels = rubric["meta"]["levels"]

    existing, existing_keys = load_existing()
    specs = collect_specs(rubric)

    if args.competency:
        specs = [s for s in specs if s["competency"]["id"] == args.competency]
        if not specs:
            all_ids = [s["competency"]["id"] for s in collect_specs(rubric)]
            print(f"Competency '{args.competency}' not found. Available:\n  " + "\n  ".join(all_ids))
            sys.exit(1)

    if args.level:
        if args.level not in levels:
            print(f"Level '{args.level}' not found. Available: {levels}")
            sys.exit(1)
        levels = [args.level]

    if args.force:
        force_keys = {
            (s["competency"]["id"], lvl, s["track"])
            for s in specs for lvl in levels
        }
        existing = [c for c in existing if (c["competency_id"], c["level"], c["track"]) not in force_keys]
        existing_keys -= force_keys
        print(f"  ⚠ --force: dropping {len(force_keys)} card set(s) for regeneration")

    system = SYSTEM_PROMPT.replace("{n}", str(CARDS_PER_LEVEL))
    new_cards = []

    for spec in specs:
        comp, caps, track, placeholder = (
            spec["competency"], spec["capabilities"],
            spec["track"], spec["is_placeholder"]
        )
        label = f"{comp['id']} [{track or 'shared'}]"

        for level in levels:
            if (comp["id"], level, track) in existing_keys:
                print(f"  ↷ skip      {label} / {level}")
                continue

            if args.dry_run:
                print(f"  ○ would gen {label} / {level}")
                print(build_user_prompt(comp, level, caps, placeholder)[:300])
                print()
                continue

            print(f"  ⟳ generating {label} / {level} ...", end=" ", flush=True)
            try:
                raw   = call_api(system, build_user_prompt(comp, level, caps, placeholder), args.provider)
                cards = parse_cards(raw, comp["id"], level, track, placeholder)
                new_cards.extend(cards)
                print(f"✓ {len(cards)} cards")
            except Exception as e:
                print(f"✗ {e}")

            time.sleep(0.5)

    if args.dry_run:
        print(f"\n(dry-run — {OUTPUT_PATH.name} not written)")
        return

    all_cards = existing + new_cards
    save_cards(all_cards)
    print(f"\n✓ {OUTPUT_PATH.name}  ({len(all_cards)} total, {len(new_cards)} new)")

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate evaluation wizard example cards from rubric.json."
    )
    parser.add_argument("--competency", metavar="ID",
                        help="Limit to one competency (e.g. ownership-autonomy)")
    parser.add_argument("--level", metavar="LN",
                        help="Limit to one level (e.g. L4); use with --competency")
    parser.add_argument("--force", action="store_true",
                        help="Drop and regenerate matching existing cards")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview prompts without calling the API")
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--anthropic", dest="provider", action="store_const", const="anthropic",
                          help="Use Anthropic API / ANTHROPIC_API_KEY (default)")
    provider.add_argument("--openai",    dest="provider", action="store_const", const="openai",
                          help="Use OpenAI API / OPENAI_API_KEY")
    parser.set_defaults(provider="anthropic")
    generate(parser.parse_args())
