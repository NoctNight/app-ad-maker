---
name: app-ad-maker
description: Make a 1080x1920 short-form video ad (TikTok, Instagram Reels, YouTube Shorts) for a product or app from a one-line brief, with a scripted voiceover, generated visuals and burned-in captions, rendered locally with ffmpeg. Use when the user asks for a product ad, promo video, app ad, vertical video ad, or a TikTok/Reels/Shorts clip. Works for free with no API keys; paid providers are optional.
license: MIT
compatibility: Needs Python 3.10+ and a shell. ffmpeg is bundled via pip. Network access for the free providers; none for the offline preset.
---

# app-ad-maker

Turns a product brief into a finished vertical MP4 ad. Pipeline: script model →
storyboard → image model (or video model) → TTS voiceover → captions → ffmpeg.
The measured voiceover length drives the edit, so pacing matches the read.

All commands below run through `scripts/admaker.sh` in this skill's folder. It
creates a private `.venv` on first use (about a minute), then runs
`python -m admaker`. Call it by its full path from the user's working
directory so that output lands there.

## 1. Gather the brief

Ask only for what's missing. `--product` is the only required field, but ads
are much better with the rest:

| Flag | What to ask | Example |
|---|---|---|
| `--product` | Product or app name | `"Nomad Brew"` |
| `--description` | What it actually is, one line | `"a collapsible silicone pour-over dripper"` |
| `--audience` | Who it's for | `"campers and commuters"` |
| `--point` | 2-3 concrete selling points (repeat the flag) | `"Folds flat into any pocket"` |
| `--cta` | Call to action, usually a URL or store name | `"Get it at nomadbrew.co"` |
| `--platform` | `tiktok`, `reels`, `shorts` or `universal` (default; safe for all three) | |

Optional: `--tone` (default `energetic`), `--style` (visual direction for the
image model), `--language`, `--scenes` (default 5), `--accent "#RRGGBB"`,
`--font <family or .ttf>`, `--music <file>`, `--transition`.

## 2. Pick a provider preset — never spend money silently

| Preset | Cost | Needs |
|---|---|---|
| `free` (default) | $0 | nothing |
| `free-plus` | $0 | `GROQ_API_KEY` (free signup, no card); much better script |
| `cheap` | ~$0.05/ad | `ANTHROPIC_API_KEY` + `FAL_KEY` |
| `premium` | $1.50–9/ad | `ANTHROPIC_API_KEY` + `FAL_KEY` + `ELEVENLABS_API_KEY`; real generated video |
| `offline` | $0 | nothing; silent template ad to test the install, not for posting |

Rules:
- Use `free`, or `free-plus` if `GROQ_API_KEY` is set. Check with
  `[ -n "$GROQ_API_KEY" ]` or look for a `.env` in the working directory.
- Use `cheap` or `premium` only after the user has said yes to the cost
  shown above for this run. A key being set is not consent to spend.
- Never ask the user to paste an API key into chat. Tell them which env var
  to set (or to add it to a `.env` in the working directory) and stop.
- `scripts/admaker.sh providers` lists every provider, its cost and signup link.

## 3. Run it

```bash
<skill-dir>/scripts/admaker.sh make \
  --product "Nomad Brew" \
  --description "a collapsible silicone pour-over coffee dripper" \
  --audience "campers and commuters" \
  --point "Folds flat into any pocket" \
  --point "Brews a real cup in 90 seconds" \
  --cta "Get yours at nomadbrew.co" \
  --platform universal --preset free \
  --out ./ads/nomad-brew.mp4 --json ./ads/nomad-brew.json
```

A free run takes about 2-4 minutes, mostly waiting on rate limits and
rendering, so run it in the background if your harness allows and don't
re-launch it while it is working. Progress goes to stderr. Exit code 0 means
success; 1 prints `Failed: <reason>` (usually a missing or bad key, or a
rate limit — retry once, then fall back to `free` or `offline`).

If the first run fails on install, run `<skill-dir>/scripts/admaker.sh doctor`.

## 4. Report back

From stdout and the `--json` file, give the user:
- the MP4 path and duration,
- the script, scene by scene (`storyboard.scenes[].narration` in the JSON),
- the suggested hashtags,
- any `!` warnings (for example a provider that fell back to a free one).

Offer one quick revision: a different tone, sharper selling points, another
platform, or a brand accent colour. Remind them to check each provider's
licence before running the ad commercially; some free tiers restrict this.

## Don'ts

- Don't run `admaker serve` for the user; the web app has no auth and is for
  local manual use only.
- Don't invent claims (prices, stats, awards) the user didn't give you. The
  script model is told to stay within the brief; keep your inputs factual too.
