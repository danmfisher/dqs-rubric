# DQS Engineering Rubric

Danielle's engineering performance rubric — interactive editor, evaluation wizard, and static preview for sharing.

---

## Live preview

**https://danmfisher.github.io/dqs-rubric/**

---

## Local development

Start the interactive editor (requires Python 3):

```bash
cd rubric-editor
./start.sh
# → opens at http://localhost:8787
```

---

## Updating the static preview (GitHub Pages)

After editing `rubric_artifacts/rubric.json` or `rubric_artifacts/philosophy.md`, regenerate and commit:

```bash
python3 rubric_artifacts/src/freeze.py
git add rubric_artifacts/ index.html
git commit -m "Update rubric content"
git push
```

---

## Generating evaluation wizard example cards

Cards are stored in `examples.json`, keyed by competency, level, and track.
Re-run whenever `rubric.json` changes.

```bash
# Using OpenAI (gpt-4o)
export OPENAI_API_KEY=sk-...
python3 rubric_artifacts/src/generate_examples.py --openai

# Using Anthropic (claude-opus-4-5)
export ANTHROPIC_API_KEY=sk-ant-...
python3 rubric_artifacts/src/generate_examples.py --anthropic

# Single competency (useful for testing or targeted refresh)
python3 rubric_artifacts/src/generate_examples.py --openai --competency ownership-autonomy

# Preview prompts without calling the API
python3 rubric_artifacts/src/generate_examples.py --openai --dry-run
```

The script is incremental — it skips competency/level pairs already present in
`examples.json`. To force a full regeneration, delete `examples.json` first.

Any guidance or redirects from review sessions are captured in the
`GENERATION_NOTES` block at the top of `generate_examples.py` so that
re-runs reproduce the same quality without repeating the conversation.

---

## Repo structure

```
README.md
index.html                        static frozen preview (GitHub Pages)
rubric_artifacts/
  rubric.json                     source of truth for all rubric content
  philosophy.md                   design philosophy (17 principles)
  examples.json                   evaluation wizard behavior cards (generated)
  src/
    freeze.py                     bakes rubric.json + philosophy.md → index.html
    generate_examples.py          generates examples.json via LLM API
rubric-editor/
  public/index.html               full interactive editor UI
  server.py                       local dev server
  start.sh                        server launcher
source-docs/                      original reference materials (not deployed)
  FlexGen Leveling Guide.docx
  Job Roles.xlsx
```
