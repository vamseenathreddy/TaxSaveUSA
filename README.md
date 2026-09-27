# Shorts Automation - US Finance/Tax Tips Channel

Automated pipeline: git-tracked content queue -> TTS voiceover -> FFmpeg
render -> scheduled YouTube upload, running on GitHub Actions (no server
to maintain).

## How the staged rollout works

`config/rollout.json` defines three stages by days-since-launch:

| Stage | Days | Posts/day | US windows (ET) |
|---|---|---|---|
| stage_1_validate | 0-13 | 1 | evening only |
| stage_2_scale_2to3 | 14-27 | 3 | morning, lunch, evening |
| stage_3_full | 28+ | 5 | + afternoon |

`scripts/scheduler.py` runs every 30 min via the workflow, works out
today's stage, picks that day's posting times as **random minutes
inside the US-Eastern windows** (not fully random across the day -
this is what "spread across proven windows" means, vs pure random
timing which just adds noise you can't learn from). It's DST-safe
(uses `zoneinfo`/`America/New_York`, not a fixed UTC offset).

**Before moving to the next stage**, manually check the three
conditions in `config/rollout.json` -> `advance_conditions_manual_check`
(retention, no Content ID/strikes, no reused-content notice). This is
intentionally not automatic - a metrics dip is a reason to freeze or
roll back a stage, not something to script past.

## One-time setup

1. **Create the YouTube channel** (you're doing this manually).
2. **Google Cloud project**: enable "YouTube Data API v3", create an
   OAuth 2.0 Desktop client, download its JSON as `client_secret.json`.
3. Run `python3 scripts/youtube_auth_bootstrap.py` locally (opens a
   browser, sign in as the channel's account) - it prints a refresh
   token.
4. In the GitHub repo -> Settings -> Secrets and variables -> Actions,
   add: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
5. Delete your local `client_secret.json` - never commit it.
6. Set `rollout_start_date` in `config/rollout.json` to the date you
   actually start publishing.
7. Drop at least one background clip into `assets/backgrounds/`
   (`default.mp4`) - pull from Drive, or your own footage/stock.
8. Wire a real TTS call into `synthesize_voiceover()` in
   `scripts/generate_video.py` (ElevenLabs or Google Cloud TTS - see
   the docstring in that file for both snippets). It currently raises
   on purpose so a missing voiceover can't silently ship a silent video.
9. Add real, fact-checked topics to `content/topics.json`, sourced
   only from irs.gov / ssa.gov / usa.gov / treasury.gov - the seed
   entry is a placeholder and will fail loudly if left as-is.

## Running it

- Push to `main` with the above secrets set, and the
  `.github/workflows/publish-shorts.yml` cron takes over - checks
  every 30 min, publishes when a picked slot is crossed.
- Test manually any time via the Actions tab -> "publish-shorts" ->
  "Run workflow" (uses `workflow_dispatch`).
- `state/schedule_<date>.json` is committed back by the workflow each
  run - it's your audit trail of what was scheduled/fired each day.

## Compliance notes baked into the templates

- Every video description includes a "general education, not
  personalized advice" disclaimer - keep this on every upload.
- `categoryId: "27"` (Education) is set by default in the upload
  script - revisit if a different category fits better.
- Facts belong in `content/topics.json` sourced from primary
  government sources only - this is the highest-risk part of the
  pipeline for factual errors and is worth a periodic manual audit
  independent of the automation.
