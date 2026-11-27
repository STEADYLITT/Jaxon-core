# Business lines: what each one needs in Railway

Paste values only in **Railway → Jaxon-core → Variables**, and seal secrets. Click steps for the built connectors are in [`CONNECTORS.md`](CONNECTORS.md).
- **"built":** setting the variable turns the connection LIVE. Check it with **Test** in the console.
- **"not built yet":** Jackson already plans and drafts for that line, and shows the variable as missing. The connector itself comes in a later PR, so setting the variable today changes nothing.

| Line | Variables | Status |
|---|---|---|
| **All lines** (thinking) | `ANTHROPIC_API_KEY` | built (already set) |
| **R&D / New Ideas** | `ANTHROPIC_API_KEY` | built. Research reads the links you include in a command. |
| **Steady Trades University** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | built: posting |
| | `RESEND_API_KEY`, `RESEND_FROM`, `RESEND_WEBHOOK_SECRET` | built: email and funnels |
| | `WHOP_API_KEY` | **not built yet**: real Replay Trainer and course revenue (revenue only from Whop) |
| **Agency** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER`, `RESEND_API_KEY`, `RESEND_FROM` | built: deliverables, outreach |
| | (website hosting) | not built yet: sites are built and tested, not published |
| **Clip Farming** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | built: posting to TikTok, Shorts and Reels |
| | `FAL_KEY` | built: video |
| | `YOUTUBE_API_KEY`, `TWITCH_CLIENT_ID` | **not built yet**: finding clippable moments |
| | `CLIP_PAYOUTS_SOURCE` | **not built yet**: real views and payouts |
| **Creator Agency** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER`, `RESEND_API_KEY`, `RESEND_FROM` | built: posts and messages, always after approval |
| | `CREATOR_EARNINGS_SOURCE` | **not built yet**: real creator earnings |
| **AI Influencers** | `FAL_KEY` | built: image and video, with the cost approved first |
| | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | built: posting labeled as AI |
| | `PLATFORM_ANALYTICS` | **not built yet**: follower growth |
| **E-commerce** | `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` | built: drafts; publishing only inside a Launch |
| **Finance & Ops** | none | Uses everything above. Revenue becomes real when Whop or Shopify report it, or when you record a referenced entry. |
