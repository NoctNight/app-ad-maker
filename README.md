# admaker

Generate short-form product ads for **TikTok, Instagram Reels and YouTube
Shorts** from a one-line product description. Paste an API key (or don't —
the default stack is free and needs no account), describe what you're
selling, and get a 1080×1920 MP4 with a scripted voiceover, animated
visuals and burned-in captions.

```
   brief ──▶ script model ──▶ storyboard ──▶ image model ──▶ stills
                                    │                          │
                                    └──▶ TTS ──▶ narration ────┤
                                                               ▼
                            captions + music ──▶ ffmpeg ──▶ ad.mp4
```

The voiceover's real measured duration drives the edit, so the pacing
always matches the read.

---

## Quick start

```bash
pip install -r requirements.txt

# Check the install (bundles its own ffmpeg — nothing else to set up)
python -m admaker doctor

# Make an ad. This costs nothing and needs no account.
python -m admaker make \
  --product "Nomad Brew" \
  --description "a collapsible silicone pour-over coffee dripper" \
  --audience "campers and commuters" \
  --point "Folds flat into any pocket" \
  --point "Brews a real cup in 90 seconds" \
  --cta "Get yours at nomadbrew.co" \
  --preset free
```

Or use the web app:

```bash
python -m admaker serve      # http://127.0.0.1:8000
```

<!-- The UI is one page: describe the product, pick a stack, hit generate. -->

---

## Picking a stack

| Preset | What it uses | Cost | Signups |
|---|---|---|---|
| `free` | Pollinations + Edge TTS | **$0** | none |
| `free-plus` ⭐ | Groq script + Pollinations + Edge TTS | **$0** | 1 (no card) |
| `cheap` | Claude + fal.ai Flux + Edge TTS | ~$0.05/ad | 2 |
| `premium` | Claude + fal.ai + ElevenLabs + **real generated video** | $1.50–9/ad | 3 |
| `offline` | Templates + procedural art + silence | $0 | none |

```bash
python -m admaker providers        # every provider, its cost and signup link
```

**Start with `free-plus`.** The script is where ad quality is won, and
Groq's free tier upgrades it without a credit card.

`offline` renders a real video with no network at all — use it to verify
the install. Its audio track is deliberately **silent**; it exists to test
the renderer, not to be posted.

See **[PROVIDERS.md](PROVIDERS.md)** for the full research: what's actually
free, what a video second really costs, and which one-time $5 to spend.

---

## API keys

Three ways, checked in this order:

1. **The web UI** — paste into the Advanced panel. Keys stay in memory for
   that job and are never written to disk or logged.
2. **`--script-key` / `--image-key` / `--tts-key` / `--video-key`** on the CLI.
3. **Environment / `.env`** — copy `.env.example` to `.env` and fill in
   whichever you have. Everything else falls back to the free providers.

```bash
cp .env.example .env
```

---

## Common recipes

```bash
# Free, but with a much better script (one free Groq key)
python -m admaker make --product "Trail Runner X" --preset free-plus

# Best quality per dollar; a $5 fal.ai top-up covers ~100 ads
export ANTHROPIC_API_KEY=sk-ant-...
export FAL_KEY=...
python -m admaker make --product "Trail Runner X" --preset cheap

# Target one platform's exact safe area and length
python -m admaker make --product "Trail Runner X" --platform tiktok

# Real generated motion instead of Ken Burns (costs real money)
python -m admaker make --product "Trail Runner X" --preset premium

# Your own brand font, colour and music bed
python -m admaker make --product "Trail Runner X" \
  --font ~/fonts/Anton-Regular.ttf --accent "#00E5FF" --music bed.mp3

# Mix and match any providers
python -m admaker make --product "Trail Runner X" \
  --script gemini --image pollinations --tts elevenlabs --voice 21m00Tcm4TlvDq8ikWAM
```

---

## What comes out

`output/ad_<timestamp>/` contains:

| File | |
|---|---|
| `<slug>.mp4` | the finished ad — H.264 + AAC, `+faststart` |
| `storyboard.json` | the script, scene by scene, with the hashtags |
| `captions.ass` | the subtitle file, if you want to restyle and re-burn |
| `visual.mp4`, `voice.m4a` | the picture and audio stems |

---

## How it works

| Module | |
|---|---|
| `admaker/models.py` | brief, storyboard, platform presets, render options |
| `admaker/providers/` | pluggable script / image / TTS / video backends |
| `admaker/render.py` | Ken Burns, transitions, audio mixing, encoding |
| `admaker/ass_subs.py` | caption timing and styling (libass) |
| `admaker/pipeline.py` | the orchestrator |
| `admaker/server.py` + `web/` | the web app |

**Platform presets** aren't just aspect ratios — each one encodes how much
of the frame that app's own UI covers, and captions are placed to clear it.
`universal` uses the strictest safe area of all three, so one render is
postable everywhere.

**Captions** are timed by distributing phrase chunks across each scene's
measured audio in proportion to character count. Without forced alignment
this isn't word-perfect, but narration is read at a near-constant character
rate, so it tracks the voice closely. Chunks containing numbers, `%` or `$`
are highlighted in the accent colour.

**Text rendering** goes through libass rather than ffmpeg's `drawtext` —
libass handles wrapping and per-word animation, and is present in far more
ffmpeg builds.

### Adding a provider

Subclass the right base, fill in `ProviderInfo`, add `@register`. It shows
up in the CLI and the web UI automatically.

```python
@register
class MyImages(ImageProvider):
    info = ProviderInfo(name="mine", kind=IMAGE, label="My API",
                        requires_key=True, env_var="MY_KEY", tier="paid")

    def generate(self, prompt, out_path, width, height, seed=0):
        ...
        return str(out_path)
```

---

## Requirements

Python 3.10+. `pip install -r requirements.txt` pulls in a static **ffmpeg**
(`imageio-ffmpeg`), so there is no system dependency to install. To use a
system ffmpeg instead, set `ADMAKER_FFMPEG=/path/to/ffmpeg`.

Optional: `pip install anthropic` for the Claude script provider.

## Limits worth knowing

- Rendering is CPU-bound: expect roughly 1–2× realtime per ad on a laptop.
- Free providers rate-limit. A 5-scene ad on the `free` preset takes a
  couple of minutes, mostly spent waiting politely.
- The web app keeps jobs in memory and has no auth — run it locally, not
  as a public service.
- Captions are phrase-level, not word-level (no forced alignment).
- Check each provider's licence before using output commercially. Several
  free tiers restrict commercial use or watermark exports.

## Licence

MIT
