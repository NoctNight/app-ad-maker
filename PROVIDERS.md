# Which AI should actually make your ads

Research notes behind the provider choices in this app. Prices checked
August 2026 — they move fast, so treat them as the shape of the market
rather than a quote.

---

## The finding that drives everything else

**True text-to-video cannot fit a $5/month budget.** The cheapest usable
model is around **$0.05 per second of output**, and the good ones are
$0.22–$0.34/s:

| Model (via fal.ai) | Per second | A single 30s ad |
|---|---|---|
| Wan 2.6 | ~$0.05 | ~$1.50 |
| Kling 3.0 Pro (no audio) | ~$0.224 | ~$6.70 |
| Kling 3.0 Pro (with audio) | ~$0.336 | ~$10 |
| Seedance 2.0 (1080p) | ~$0.30–0.68 | ~$9–20 |
| Veo 3.1 | ~10× Kling | ~$60+ |

So a $5 monthly budget buys **three ads a month** on the cheapest model, or
**less than one** on a good one. Reported per-second rates also disagree
between sources depending on resolution tier and whether audio is on — the
headline rate usually buys a lower tier than you expect.

Meanwhile, most product ads that actually perform on these platforms are
not generated video at all. They are **a sequence of strong stills with a
slow push-in, a confident voiceover, and big captions**. That is what this
app builds by default, and it costs **$0.00**.

Generated video is wired up as an optional upgrade (`--video fal`) for when
one hero ad justifies a few dollars.

---

## Recommended stacks

### 1. Free, zero signups — `--preset free`

| Stage | Provider | Cost |
|---|---|---|
| Script | Pollinations | $0, no key |
| Images | Pollinations (Flux) | $0, no key |
| Voice | Edge Neural TTS | $0, no key |

Nothing to sign up for. Pollinations rate-limits anonymous users to roughly
one request per 15 seconds, so a 5-scene ad takes a couple of minutes. Its
uptime has no SLA — the app falls back to a template script if it is down.

### 2. Free, one signup, noticeably better — `--preset free-plus` ⭐ start here

| Stage | Provider | Cost |
|---|---|---|
| Script | **Groq** | Free tier, no card |
| Images | Pollinations (Flux) | $0, no key |
| Voice | Edge Neural TTS | $0, no key |

The script is where ad quality is won or lost, and it is also the cheapest
thing to upgrade. Groq's free tier runs Llama 3.3 70B at high speed with
generous daily limits and **no credit card**. This is the best
quality-per-effort point in the whole table.

Get a key: <https://console.groq.com/keys>

### 3. Best value if you'll spend anything — `--preset cheap`

| Stage | Provider | Cost |
|---|---|---|
| Script | **Claude** (Haiku or Opus) | ~$0.002–0.03 per script |
| Images | **fal.ai** (Flux schnell) | ~$0.003–0.025 per image |
| Voice | Edge Neural TTS | $0 |

**A one-off $5 top-up on fal.ai covers roughly 100 ads** at 5 images each.
Both fal.ai and Anthropic are pay-as-you-go with **no subscription**, which
is what you asked for — you top up once and it drains slowly.

---

## Full comparison by stage

### Script writing

| Provider | Free tier | Card needed | Notes |
|---|---|---|---|
| **Groq** | Yes, generous daily | No | Fastest free option. Best free pick. |
| **Google Gemini** | Yes, daily quota on Flash | No | AI Studio key, no card. Strong writer. |
| **OpenRouter** | Yes — `:free` models | No | One key, hundreds of models. |
| **Pollinations** | Yes, unlimited-ish | No key at all | Rate limited, no SLA. |
| Claude | No | Yes | Best ad copy of the group. Cents per script. |
| OpenAI | No | Yes | Solid, comparable pricing. |

### Images

| Provider | Free tier | Notes |
|---|---|---|
| **Pollinations (Flux)** | Yes, no key | Genuinely free Flux. Register (free) to lift limits and drop the watermark. |
| **Google Gemini image** | Free tier on AI Studio keys | Good quality, one key shared with scripting. |
| fal.ai (Flux) | No | ~$0.003–0.025/image. Cheapest reliable paid option. |
| OpenAI `gpt-image-1` | No | ~$0.02–0.19/image. Excellent prompt adherence, priciest here. |

⚠️ Every image model renders text as convincing gibberish. This app
explicitly instructs the model to put **no text in the frame** and burns all
captions on afterwards with libass.

### Voiceover

| Provider | Free tier | Notes |
|---|---|---|
| **Edge Neural TTS** | Unlimited, no key | Microsoft's read-aloud voices. Quality is close to paid services. The default, and hard to beat. |
| **Google Cloud TTS** | 1M WaveNet chars/month | The most generous free tier of any paid-grade TTS. Needs a GCP project. |
| Amazon Polly | 5M standard chars/month for 12 months, then 1M neural/month | Generous but AWS setup is heavier. |
| ElevenLabs | ~10k chars/month (~25 short ads) | The most expressive voices by a distance. Paid from $5/month. |
| OpenAI TTS | No | ~$0.015/1k chars — well under a cent per ad. |

An ad script is only ~70 words (~400 characters), so **even ElevenLabs'
free tier covers about 25 ads a month**.

### True text-to-video (optional, expensive)

| Provider | Notes |
|---|---|
| **fal.ai** | Widest model selection behind one pay-as-you-go API. Start with Wan for cost. |
| Replicate | Same model families, async polling, comparable pricing. |
| Kling AI (web app) | 66 free daily credits — the most generous free tier, but it's a **web UI, not an API**, and free exports are watermarked. |
| Google AI Studio | 2–5 free video generations/day in the UI. Veo is **not** on the Gemini API free tier — API access is paid per second. |

Most free video tiers watermark exports and several restrict output to
non-commercial use — check before putting one in a product ad.

---

## One-time costs worth paying

You asked specifically about one-off spends. In order of value:

1. **$5 on fal.ai** — ~100 ads' worth of Flux images, or ~3 Wan video clips.
   No subscription, no expiry pressure. *This is the single best $5 here.*
2. **$5 on Anthropic** — hundreds of ad scripts on Haiku, dozens on Opus.
3. **A font** ($0–20) — pass `--font /path/to/Font.ttf`. A distinctive
   caption face does more for perceived production value than a better
   image model. Free options: Inter, Montserrat, Bebas Neue, Anton
   (Google Fonts).
4. **Music** ($0) — a royalty-free bed via `--music track.mp3`. Check the
   licence covers commercial use.

**Not worth it for this use case:** monthly video-generator subscriptions
($20–250/mo). You are paying for a web editor you don't need, and the
per-ad cost still beats them at pay-as-you-go rates.

---

## Sources

- [Free AI Video Generation API: What's Actually Free in 2026 — Apiframe](https://apiframe.ai/blog/free-ai-video-generation-api-2026)
- [AI Video API Pricing 2026: Seedance vs Sora vs Kling vs Veo — DevTk](https://devtk.ai/en/blog/ai-video-generation-pricing-2026/)
- [fal.ai Alternatives (2026): 6 Video APIs by Per-Second Cost — ofox.ai](https://ofox.ai/blog/fal-ai-alternatives-video-generation-api-2026/)
- [AI Image & Video APIs 2026: FAL vs Replicate vs OpenAI — Teamday](https://www.teamday.ai/blog/ai-image-video-api-providers-comparison-2026)
- [Google Veo 3 Pricing 2026: Free AI Studio Limits & Paid Access](https://www.veo3ai.io/blog/google-veo-3-pricing-free-or-paid-2026)
- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [14 Best Free AI Video Generators in 2026 (Tested & Ranked)](https://aivideopicks.com/posts/best-free-ai-video-generators-2026.html)
- [5 Best Free Text-to-Speech AI APIs in 2026 — CAMB.AI](https://www.camb.ai/blog-post/best-free-text-to-speech-ai-apis)
- [Best Free Text-to-Speech APIs in 2026: Limits Compared — FreeAPIHub](https://freeapihub.com/blog/best-free-text-to-speech-apis)
- [Pollinations — open-source gen-AI platform (GitHub)](https://github.com/pollinations/pollinations)
- [Free AI Image Generation API in 2026 — Apiframe](https://apiframe.ai/blog/free-ai-image-generation-api-2026)
