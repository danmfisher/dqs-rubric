#!/usr/bin/env python3
"""
freeze.py — Generates a self-contained, read-only HTML preview of the rubric.

Usage:
    python3 freeze.py

Output:
    index.html  (in the same directory as this script — served by GitHub Pages)

Run this any time you want to update the GitHub Pages file with the latest
rubric.json and philosophy.md content.
"""

import json, re, copy
from pathlib import Path

BASE         = Path(__file__).parent          # rubric_artifacts/src/
ARTIFACTS    = BASE.parent                    # rubric_artifacts/
ROOT         = BASE.parent.parent             # project root
RUBRIC_PATH    = ARTIFACTS / "rubric.json"
PHIL_PATH      = ARTIFACTS / "philosophy.md"
EXAMPLES_PATH  = ARTIFACTS / "examples.json"
SOURCE_HTML  = ROOT / "rubric-editor" / "public" / "index.html"
OUTPUT_HTML  = ROOT / "index.html"


# ── Replicate server's resolve_track() ──────────────────────────────────────
def resolve_track(rubric, track_id):
    r = copy.deepcopy(rubric)
    r["meta"]["current_track"] = track_id
    for dim in r["dimensions"]:
        for comp in dim["competencies"]:
            for cap in comp["capabilities"]:
                if cap.get("track_specific") and isinstance(
                    list(cap["descriptions"].values())[0], dict
                ):
                    cap["descriptions"] = cap["descriptions"].get(
                        track_id,
                        cap["descriptions"].get("swe-ic", {})
                    )
    return r


# ── Replicate server's read_philosophy() ────────────────────────────────────
def read_philosophy():
    text = PHIL_PATH.read_text(encoding="utf-8")
    tokens = []
    for m in re.finditer(
        r'(?:<!-- group:\s*(.+?)\s*-->|^## (\d+)\.\s+(.+?)$(.*?)(?=^## |<!-- group:|\Z))',
        text, re.MULTILINE | re.DOTALL
    ):
        if m.group(1) is not None:
            tokens.append(("group", m.group(1)))
        else:
            tokens.append(("section", m.group(2), m.group(3).strip(), m.group(4).strip()))
    sections, current_group = [], ""
    for tok in tokens:
        if tok[0] == "group":
            current_group = tok[1]
        else:
            sections.append({
                "number":  tok[1],
                "title":   tok[2],
                "content": tok[3],
                "group":   current_group,
            })
    return sections


# ── Build baked data ─────────────────────────────────────────────────────────
print("Reading rubric.json, philosophy.md, and examples.json...")
with open(RUBRIC_PATH, encoding="utf-8") as f:
    rubric = json.load(f)

available_tracks = [t["id"] for t in rubric["meta"]["tracks"] if t.get("available")]
tracks_data = {track: resolve_track(rubric, track) for track in available_tracks}
philosophy  = read_philosophy()
examples    = json.loads(EXAMPLES_PATH.read_text(encoding="utf-8")) if EXAMPLES_PATH.exists() else []
baked       = json.dumps({"tracks": tracks_data, "philosophy": philosophy, "examples": examples})
print(f"  Tracks baked: {available_tracks}")
print(f"  Philosophy sections: {len(philosophy)}")
print(f"  Example cards: {len(examples)}")


# ── Read source HTML ─────────────────────────────────────────────────────────
src = SOURCE_HTML.read_text(encoding="utf-8")


# ── Inject baked data after the opening babel script tag ────────────────────
src = src.replace(
    '<script type="text/babel">',
    f'<script type="text/babel">\nwindow.BAKED = {baked};\n',
    1
)


# ── Replace fetch() calls with baked lookups ─────────────────────────────────

# Initial rubric load
src = src.replace(
    "fetch('/api/rubric')\n"
    "      .then(r => r.json())\n"
    "      .then(data => {\n"
    "        setRubric(data);\n"
    "        setSelectedTrack(data.meta.current_track || data.meta.default_track || 'swe-ic');\n"
    "        setLoading(false);\n"
    "      })\n"
    "      .catch(err => { showToast('Failed to load rubric: ' + err.message, 'error'); setLoading(false); });",

    "Promise.resolve(window.BAKED.tracks['swe-ic'])\n"
    "      .then(data => {\n"
    "        setRubric(data);\n"
    "        setSelectedTrack(data.meta.current_track || data.meta.default_track || 'swe-ic');\n"
    "        setLoading(false);\n"
    "      })\n"
    "      .catch(err => { setLoading(false); });"
)

# Track change fetch
src = src.replace(
    "fetch(`/api/rubric?track=${encodeURIComponent(trackId)}`)\n"
    "      .then(r => r.json())\n"
    "      .then(data => { setRubric(data); setLoading(false); })\n"
    "      .catch(err => { showToast('Failed to load track: ' + err.message, 'error'); setLoading(false); });",

    "Promise.resolve(window.BAKED.tracks[trackId] || window.BAKED.tracks['swe-ic'])\n"
    "      .then(data => { setRubric(data); setLoading(false); })\n"
    "      .catch(() => { setLoading(false); });"
)

# Philosophy fetch
src = src.replace(
    "fetch('/api/philosophy')\n"
    "      .then(r => r.json())\n"
    "      .then(data => Array.isArray(data) && setPhilosophy(data))\n"
    "      .catch(() => {});",

    "Promise.resolve(window.BAKED.philosophy)\n"
    "      .then(data => Array.isArray(data) && setPhilosophy(data));"
)

# Examples fetch (wizard card exercise)
src = src.replace(
    "fetch('/api/examples')\n"
    "      .then(r => r.json())\n"
    "      .then(data => {",

    "Promise.resolve(window.BAKED.examples || [])\n"
    "      .then(data => {"
)

# Save handler (no server in preview — just clear dirty state)
src = src.replace(
    "fetch('/api/rubric', {\n"
    "      method: 'PUT',\n"
    "      headers: { 'Content-Type': 'application/json' },\n"
    "      body: JSON.stringify(updatedRubric)\n"
    "    })\n"
    "      .then(r => r.json())\n"
    "      .then(result => {\n"
    "        if (result.error) throw new Error(result.error);\n"
    "        setSaveStatus('saved');\n"
    "        showToast('Saved successfully');\n"
    "      })\n"
    "      .catch(err => {\n"
    "        setSaveStatus('error');\n"
    "        showToast('Save failed: ' + err.message, 'error');\n"
    "      });",

    "Promise.resolve().then(() => { setSaveStatus('saved'); });"
)


# ── Lock editing permanently ─────────────────────────────────────────────────
src = src.replace(
    "const [editingEnabled, setEditingEnabled] = useState(false);",
    "const editingEnabled = false;"
)

# Disable lock toggle (static icon, no click handler)
src = src.replace(
    """          <span
            className="tab-lock"
            title={editingEnabled ? 'Lock editing' : 'Unlock editing'}
            onClick={e => {
              e.stopPropagation();
              setEditingEnabled(v => !v);
            }}
          >
            {editingEnabled ? <UnlockedIcon /> : <LockedIcon />}
          </span>""",
    """          <span style={{ marginLeft: 6, opacity: 0.4 }} title="Editing disabled in preview">
            <LockedIcon />
          </span>"""
)


# NOTE: Rubric CSV export and philosophy .md download are now fully client-side
# (Blob + URL.createObjectURL) — no stubs needed here.


# ── Write output ─────────────────────────────────────────────────────────────
OUTPUT_HTML.write_text(src, encoding="utf-8")
kb = OUTPUT_HTML.stat().st_size // 1024
print(f"\n✓ Written: {OUTPUT_HTML.name}  ({kb} KB)")


# ── Verify ───────────────────────────────────────────────────────────────────
checks = [
    ("Danielle's in title",        "Danielle's Technology Rubric" in src),
    ("FlexGen not in title",       "FlexGen Technology Rubric" not in src),
    ("editingEnabled constant",    "const editingEnabled = false;" in src),
    ("no setEditingEnabled",       "setEditingEnabled" not in src),
    ("no server fetch calls",      "fetch('/api/" not in src and "fetch(`/api/" not in src),
    ("BAKED data injected",        "window.BAKED" in src),
    ("philosophy sections baked",  str(len(philosophy)) in baked),
    ("rubric CSV export is client-side", "exportRubricCSV" in src),
    ("philosophy download client-side",  "downloadPhilosophy" in src),
    ("examples baked",                   f'"examples":{len(examples)}' in baked.replace(' ', '') or '"examples":' in baked),
    ("examples fetch patched",           "window.BAKED.examples" in src),
]
print()
all_passed = True
for label, result in checks:
    print(f"  {'✓' if result else '✗'} {label}")
    if not result:
        all_passed = False

print()
print("✓ All checks passed — ready to deploy." if all_passed else "✗ Some checks failed — review above.")
