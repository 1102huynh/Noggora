# Local fallback assets

`videos/bg_*.mp4` — subtly animated dark gradient loops (ffmpeg `gradients`
source + grain/vignette), used as B-roll whenever `PEXELS_API_KEY` is unset or
a Pexels search comes back short. Good enough to publish as-is, not just a
test fixture — but real B-roll from Pexels (people, brains, real footage)
will generally perform better. `visual_fetcher.py` automatically prefers
Pexels the moment a key is set in `.env`, no code changes needed.

`music/generated/mood_*.mp3` — background music is **not** stored here as
static files; `src/music_composer.py` synthesizes one track per mood (see its
`_MOODS` dict) the first time that mood is needed, then caches it here for
reuse. Fully synthesized (sine oscillators + tremolo/lowpass/echo, nothing
sampled or downloaded), so there is **zero copyright risk** — safe to ship in
published videos as-is. Delete files in `generated/` any time to force a
re-synthesize (e.g. after tweaking `_MOODS` in the code).
