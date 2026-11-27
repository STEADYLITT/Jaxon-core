# The 15 departments: what each one needs in Railway

Paste values only in **Railway → Jaxon-core → Variables**, and seal secrets. Never paste a key in chat. Click steps for the built connectors are in [`CONNECTORS.md`](CONNECTORS.md).

- **"built":** setting the variable turns the connection LIVE. Check it with **Test** in the console (Connections).
- **"not built yet":** Jackson already plans and drafts for that department and shows the variable as missing. The connector itself comes in a later PR, so setting it today changes nothing.

| # | Department | Variables | Status |
|---|---|---|---|
| 0 | **Every department** (thinking) | `ANTHROPIC_API_KEY` | built |
| | (file storage) | `S3_BUCKET` | built |
| 1 | **R&D / Opportunities** | `ANTHROPIC_API_KEY` | built. Research reads the links you put in a command. |
| 2 | **Course & Education Factory** | `RESEND_API_KEY`, `RESEND_FROM`, `RESEND_WEBHOOK_SECRET` | built: course emails and funnels |
| | | `FAL_KEY` | built: AI video for lessons, always approved first |
| | | `WHOP_API_KEY`, `WHOP_WEBHOOK_SECRET` | built: course checkout links, students with access, real course revenue |
| | | `ELEVENLABS_API_KEY` | **not built yet**: AI voice for lessons |
| 3 | **Marketing (all companies)** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | built: posting |
| | | `RESEND_API_KEY`, `RESEND_FROM` | built: email |
| | | `WHOP_API_KEY` | built: real sales for course offers |
| 4 | **Agency** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER`, `RESEND_API_KEY`, `RESEND_FROM` | built: client deliverables and outreach |
| 5 | **Sales & Lead Gen** | `RESEND_API_KEY`, `RESEND_FROM` | built: outreach email (opt-outs respected) |
| | | (SMS and calls) | **not built yet**: call lists and scripts are drafts for your team |
| 6 | **App / SaaS / Web Factory** | `SITE_DOMAIN_VERIFY` | built: domain checks |
| | | (website hosting) | **not built yet**: sites are built and tested, not published |
| | | `GITHUB_APP_TOKEN` | **not built yet**: new repos for apps |
| 7 | **Content & Clip Farming** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER`, `FAL_KEY` | built: posting and video |
| | | `YOUTUBE_API_KEY`, `TWITCH_CLIENT_ID` | **not built yet**: finding clippable moments |
| | | `CLIP_PAYOUTS_SOURCE` | **not built yet**: real views and payouts |
| 8 | **Creator Agency** | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER`, `RESEND_API_KEY`, `RESEND_FROM` | built: always after approval, SFW only |
| | | `CREATOR_EARNINGS_SOURCE` | **not built yet**: real creator earnings |
| 9 | **AI Influencers** | `FAL_KEY`, `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | built: generation (cost approved first) and posting labeled as AI |
| | | `ELEVENLABS_API_KEY` | **not built yet**: persona voice |
| | | `PLATFORM_ANALYTICS` | **not built yet**: follower growth |
| 10 | **E-commerce & Reselling** | `SHOPIFY_STORE_DOMAIN` + (`SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET`) or `SHOPIFY_ADMIN_API_TOKEN` | built: drafts; publishing only inside a Launch |
| | | `AMAZON_SP_API_KEY`, `EBAY_API_KEY` | **not built yet**: FBA and eBay research |
| 11 | **Community** | `WHOP_API_KEY` | built: paying members and their linked Discord accounts |
| | | `DISCORD_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN` | **not built yet**: role sync from Whop payments |
| 12 | **Trading** | `APEX7_RESULTS_SOURCE`, `MARKET_DATA_API_KEY` | **not built yet**: real backtest and sim results |
| | | `BROKER_API_KEY` | **not built, on purpose**: live orders stay refused until a broker connector with daily loss caps, a kill switch and your explicit approval per order exists |
| 13 | **Real Estate (wholesaling)** | `PROPERTY_DATA_API_KEY` | **not built yet**: comps; research and simulation only |
| 14 | **Legal & Admin** | none | Drafts only; always sent to you; never filed automatically |
| 15 | **Finance & Ops** | none | Uses everything above. Revenue is real only when Whop or Shopify report it, or when you record a referenced entry. |

**Fastest money first:** Shopify and Whop are both built and waiting on your keys. One Whop key unlocks Courses, Marketing sales and Community numbers.
