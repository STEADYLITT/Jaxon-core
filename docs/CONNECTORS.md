# Connecting Jackson to your accounts

Each connector stays a clearly labeled stand-in until its variables are set in **Railway → Jaxon-core → Variables**.
- Paste keys **only there, never into chat**.
- After saving, Railway redeploys (about 2 minutes).
- In the console, the **Connections** panel shows each service as:
  - **LIVE**: connected
  - **MOCK**: stand-in
  - **ERROR**: a key is set, but the test failed
- The panel also lists any missing variable names, and has a **Test** button. Test makes one harmless real call and shows the real answer.

## The checklist: one at a time, in this order
**Why this order:** Shopify comes first because it's the cash register. Nothing earns money until there's a product in your store to buy. Posting (Upload-Post) brings people to the store. Email (Resend) turns them into repeat buyers. Video (fal.ai) makes the posts better, but it costs money per clip, so it comes last.

How to paste a variable into Railway (same every time):
1. Go to **railway.com**, open the project, click the **Jaxon-core** service (not Postgres), and open the **Variables** tab.
2. Click **+ New Variable**. Type the **name** exactly as shown, paste the value, then click **Add**.
3. For keys and secrets: click **⋮** next to the variable, then **Seal**. Once sealed, the value can never be shown again, even to you.
4. Click **Deploy** if Railway asks. Wait for the green **Active** badge.
5. Open the console → **Connections**, and press **Test** next to that service.

| # | Service | Variables | Test shows |
|---|---|---|---|
| 1 | **Shopify** (steps below) | `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` | your store's name and plan (read-only) |
| 2 | **Upload-Post** (steps below) | `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | your Upload-Post plan and account email |
| 3 | **Resend** (steps below) | `RESEND_API_KEY`, `RESEND_FROM`, then `RESEND_WEBHOOK_SECRET` | sends one test email **to you only** |
| 4 | **fal.ai** (steps below) | `FAL_KEY` | your credit balance (or "key accepted") |
| 5 | **Whop** (steps below) | `WHOP_API_KEY`, then `WHOP_WEBHOOK_SECRET` | your Whop account id and name (read-only) |
| 6 | **ElevenLabs** (steps below) | `ELEVENLABS_API_KEY` | your plan and characters left |
| 7 | **Apify** (steps below) | `APIFY_TOKEN` | your Apify account and plan |
| 8 | **Discord** (steps below) | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_MEMBER_ROLE_ID`, `DISCORD_ANNOUNCE_CHANNEL_ID` | your server, member count and the member role's name |
| 9 | **Telegram** (steps below) | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | your bot and group name |
| 10 | **Netlify** (steps below) | `NETLIFY_AUTH_TOKEN` | your Netlify account name |
| 11 | **BatchData** (steps below) | `BATCHDATA_API_TOKEN`, `BATCHDATA_CENTS_PER_RECORD` | that the token and your price are set (BatchData has no free test call) |
| 12 | **Alpaca paper** (steps below) | `ALPACA_PAPER_KEY_ID`, `ALPACA_PAPER_SECRET_KEY` | your paper account number, status and equity |
| — | **Claude** | `ANTHROPIC_API_KEY` (already set) | how many models your key can use (free call) |

Not built yet: SMS/calls, so it always shows MOCK. File storage (S3) is optional, because files live on the Railway disk.

---

## Shopify (your real store, drafts only)

Store: `cha6cs-6c.myshopify.com`.
- **What Jackson does:** creates **draft** products and checks each one by reading it back from the store.
- **What needs approval:** publishing a product. It needs your approval in the inbox, or a Launch approval that allows "Publish Shopify products".

### Why a new app
- **Same organization required:** Shopify only lets Jackson fetch its own access token when the app and the store are in the **same Shopify organization**.
- **Your current app is elsewhere:** the "Jackson" app you made (org 248016126) offers only **Test app** on dev stores. That means it lives in a Partner organization, not your store's.
- **Fix:** make the app **from your store's admin**, so it's created in the store's organization.

### Click steps, about 5 minutes
1. Open your store admin, **cha6cs-6c.myshopify.com/admin**.
2. **Settings** (bottom left) → **Apps** → **Develop apps**.
3. If you see the **App development** page, click **Build apps in Dev Dashboard**. The Dev Dashboard opens **for your store's organization**.
4. **Create app** → under **Start from Dev Dashboard**, name it `Jackson` → **Create**.
5. Create the version:
   - **App URL:** `https://jaxon-core-production.up.railway.app/console`. Leave **Embed app in Shopify admin** off.
   - **Webhooks API version:** pick the latest.
   - **Access → Scopes:** `read_products,write_products`
   - Click **Release**, then **Release** again.
6. On the app's page, open **Installs** → **Install app** → choose **cha6cs-6c** → **Install**.
7. Open the app's **Settings** and copy the **Client ID** and the **Client secret**.
8. In **Railway → Jaxon-core → Variables**, add the three Shopify variables (listed below) and **Seal** the secret.
9. Railway redeploys. In the console, **Connections → Shopify store**, press **Test**. You should see your store's name and **Basic** plan.

### If it says "the Jackson app isn't in this store's organization"
The app was created from the Partner dashboard instead of the store admin. Redo steps 2–7, starting from **your store's** Settings → Apps. You can delete the old app.

### Variables
| Name | What to paste |
|---|---|
| `SHOPIFY_STORE_DOMAIN` | `cha6cs-6c.myshopify.com` |
| `SHOPIFY_CLIENT_ID` | the app's **Client ID** (Dev Dashboard → your app → Settings) |
| `SHOPIFY_CLIENT_SECRET` | the app's **Client secret** (same page). Seal it in Railway. |

**How the token works:** Jackson exchanges the client ID and secret for an Admin API token valid 24 hours (Shopify's "client credentials grant"). It renews the token automatically about 10 minutes before expiry, or straight away if Shopify rejects it. Tokens live only in memory: never in the database, logs or grants. If you already have a static Admin API token, you can set `SHOPIFY_ADMIN_API_TOKEN` instead of the client ID and secret.

---

## Social posting via Upload-Post

**What Jackson does:** posts content you approved (or content inside an active Launch approval that names that account) to your connected social accounts. The post goes to Upload-Post's API with:
- the caption and hashtags
- any images or video, as public links
- an "AI-generated" label when the media or persona is AI-made
- a per-post Idempotency-Key, so a retry never posts twice

Every post still needs your approval or a covering Launch approval. The database refuses anything else.

### Click steps
1. Sign in at **upload-post.com**. Your Growth OS app already uses this account.
2. **Manage Users:** check the profile name your social accounts are connected under (for example `steady`). If there's no profile yet, create one and connect Instagram, TikTok, X, Facebook or YouTube there.
3. **API Keys:** copy your API key (or create one).
4. In **Railway → Jaxon-core → Variables**, add the two Upload-Post variables (listed below) and **Seal** the key.
5. After the redeploy: **Connections → Social posting → Test**. It should show your plan.

### Variables
| Name | What to paste |
|---|---|
| `UPLOADPOST_API_KEY` | your Upload-Post API key. Seal it in Railway. |
| `UPLOADPOST_USER` | the Upload-Post **profile name** your accounts are connected under |

**In a Launch approval,** list the social accounts by platform name: `instagram`, `tiktok`, `x`, `facebook`, `youtube_shorts`. Instagram, TikTok and YouTube need an image or video. Text-only posts go to X and Facebook.

---

## Email via Resend

**What Jackson does:** sends outreach campaigns you approved, or campaigns to a list named in an active Launch approval. Each email:
- goes out from your verified domain (`steadytradesuniversity.com`). A campaign that names any other sender is refused before anything is sent.
- carries one-click unsubscribe headers (`List-Unsubscribe` + `List-Unsubscribe-Post`, which Gmail and Yahoo require) as well as the unsubscribe link and postal address in the body
- has a per-message Idempotency-Key, so a retry never sends twice
- is re-checked at send time: opt-outs, the daily limit, and whether the campaign is still approved

**Bounces and spam complaints** come back through a signed webhook. Jackson opts that address out permanently. If the email was part of a launch, it also counts toward the launch's bounce (5%) and complaint (0.3%) limits, so the launch auto-pauses when either is crossed.

SMS and calls stay labeled stand-ins.

### Click steps
1. Sign in at **resend.com**. Your Growth OS app already uses this account, and `steadytradesuniversity.com` is already verified under **Domains**.
2. Go to **API Keys → Create API key**. Name it "Jackson", choose **Sending access**, and limit it to `steadytradesuniversity.com`. Copy the key.
3. Go to **Webhooks → Add webhook**:
   - **Endpoint:** once steps 2 and 4 are done (key and sender saved), the console's **Connectors** panel shows the exact URL next to "Email sending (Resend)". It looks like `https://jaxon-core-production.up.railway.app/webhooks/resend/<your tenant id>`. Copy it from there.
   - **Events:** tick `email.bounced` and `email.complained`.
   - Save, then copy the **Signing secret** (starts with `whsec_`).
4. In **Railway → Jaxon-core → Variables**, add `RESEND_API_KEY` and `RESEND_FROM` first, then do step 3 and add `RESEND_WEBHOOK_SECRET`. Seal the key and the secret.

### Variables
| Name | What to paste |
|---|---|
| `RESEND_API_KEY` | the `re_...` key from step 2. Seal it. |
| `RESEND_FROM` | `Steady <steady@steadytradesuniversity.com>` |
| `RESEND_WEBHOOK_SECRET` | the `whsec_...` signing secret from step 3. Seal it. |

**In campaigns,** set the sender email to `steady@steadytradesuniversity.com`, or any address on that domain.

---

## UGC video via fal.ai

**What Jackson does:** makes a short clip (4, 6 or 8 seconds, vertical 9:16 by default, 1080p, with sound) for a content draft, using Google Veo 3.1 Fast on fal.ai.
- **Cost first:** in the console's **Content** panel, each draft has a **Make 8s video ($1.20)** button. Clicking it sends a request to **Approvals** with the exact amount. Nothing is generated or paid for until you press **Approve**. Inside an active Launch approval, the launch's budget and daily cap count as your approval instead.
- **Prices** (fal.ai, Veo 3.1 Fast): $0.15 a second with sound, or $0.10 without, at 720p/1080p. At 4K it's $0.35 or $0.30. An 8-second clip with sound costs **$1.20**.
- **The clip becomes a new draft** with the video attached and marked AI-generated, so posting it needs its own approval. Your approved original is never changed.
- If fal is slow, Jackson checks back on the same request; it never pays twice.

### Click steps
1. Sign in at **fal.ai**. Your Growth OS app already uses this account. Make sure billing has credit.
2. Go to **fal.ai/dashboard/keys** (top right: your avatar → **API Keys**) → **Add key**. Name it "Jackson".
   - **Permissions:** choose **API**, so it can run models. If you also want the Test to show your credit balance, choose **FULL** instead.
   - Copy the key; it's shown only once.
3. In **Railway → Jaxon-core → Variables**, add `FAL_KEY` and **Seal** it.
4. After the redeploy: **Connections → UGC video → Test**. It shows your balance, or "key accepted" for an API-only key.

### Variables
| Name | What to paste |
|---|---|
| `FAL_KEY` | the fal.ai key from step 2. Seal it. |
| `FAL_VIDEO_MODEL` | optional. Leave it unset to use `fal-ai/veo3.1/fast`. If you set another model, also set `FAL_VIDEO_CENTS_PER_SECOND`, so Jackson knows the price. |

fal.ai keeps generated files on its own servers. Post a clip within a few days of making it, or ask me to add permanent storage.

---

## Whop (course checkout, students, real sales)

**What Jackson does:**
- **Students:** reads your Whop memberships. "Students with access" counts active, trialing and completed one-time members. Each member's linked Discord account is kept for role sync, which comes in a later PR.
- **Sales:** every **paid** Whop payment becomes one real revenue entry: amount, currency, month, and the Whop payment id. It's recorded once, even if both the webhook and a sync see it. Refunds are recorded as refunds. Nothing is ever estimated.
- **Checkout:** link a course to a Whop plan and Jackson asks Whop for a checkout link. The course's sales page then shows **Enroll now** instead of "Nothing can be bought". Whop runs the checkout. Publishing the page is still your launch decision.
- **Which business:** sales from Jackson's own checkout links land on the right business automatically. For products you made yourself on Whop, link each product to a business once (`POST /tenants/{t}/whop/links` with `business_id` and `product_id`). Until then, those sales count in totals but not on any one business.

### Click steps
1. Sign in at **whop.com** → **Dashboard** → **Developer** (whop.com/dashboard/developer).
2. Under **Account API Keys**, click **Create**. Name it "Jackson".
   - Permissions: pick a custom set with **member:basic:read**, **payment:basic:read** and **checkout_configuration:create**. Admin also works, but it gives more access than Jackson needs.
   - Copy the key.
3. In **Railway → Jaxon-core → Variables**, add `WHOP_API_KEY` and **Seal** it.
4. After the redeploy: **Connections → Whop → Test**. It shows your Whop account.
5. Webhook, so sales show up the moment they happen:
   - Whop **Developer** tab → **Create webhook**.
   - URL: `https://<your Railway domain>/webhooks/whop/<your tenant id>`. The console's Whop status (`GET /tenants/{t}/whop`) shows the exact URL.
   - Events: `payment.succeeded`, `refund.created`, `membership.activated`, `membership.updated`.
   - Copy the **Secret** (it starts `ws_`) into Railway as `WHOP_WEBHOOK_SECRET`, and **Seal** it.
6. Run one **Sync** (`POST /tenants/{t}/whop/sync`) to pull the members and sales you already have.

### Variables
| Name | What to paste |
|---|---|
| `WHOP_API_KEY` | the Account API key (secret, seal it) |
| `WHOP_WEBHOOK_SECRET` | the webhook's Secret, starting `ws_` (secret, seal it) |

---

## AI voice via ElevenLabs (lesson narration, ad voiceovers)

**What Jackson does:** turns a course lesson's script, or an ad script you give it, into an mp3. The clip is labeled **AI voice**.
- **Script check first:** income promises, "guaranteed", fake reviews and similar claims stop the request before anything costs money.
- **Cost before generation:**
  - Ask with `POST /tenants/{t}/voice`, giving a `voice_id` and either `text` or a `course_id` with `module` and `lesson`. You see the character count and price straight away.
  - The request waits in **Approvals** for that exact amount. Inside an active Launch, the launch's budget counts as your approval.
- **Price:** Multilingual v2 is about $0.08 per 1,000 characters. A 1,500-word lesson is about 9,000 characters, or about $0.72. Flash v2.5 is half that.
  - Choose Flash with `ELEVENLABS_MODEL=eleven_flash_v2_5`.
  - If your plan's price is different, set `ELEVENLABS_CENTS_PER_1K_CHARS`.
- **Voices:** Jackson only uses voices already in your ElevenLabs library (`GET /tenants/{t}/voice/voices`). It never clones anyone's voice. ElevenLabs forbids cloning a real person without their consent.
- A course drafted by the MOCK model is refused, so you never pay to narrate placeholder text.

### Click steps
1. Sign in at **elevenlabs.io** → left sidebar **Developers** → **API Keys** → **Create API key**. Name it "Jackson".
2. Turn on **Restrict key**, then:
   - **Text to Speech**: Access
   - **Voices**: Read
   - **User**: Read (lets the Test show your characters left)
3. Copy the key, add it in Railway as `ELEVENLABS_API_KEY`, and **Seal** it.
4. After the redeploy: **Connections → AI voice → Test**. It shows your plan and characters left.

### Variables
| Name | What to paste |
|---|---|
| `ELEVENLABS_API_KEY` | the API key (secret, seal it) |
| `ELEVENLABS_MODEL` | optional: `eleven_flash_v2_5` for the cheaper, faster voice (default `eleven_multilingual_v2`) |
| `ELEVENLABS_CENTS_PER_1K_CHARS` | optional: your plan's real price, if different |

---

## Web research via Apify (trends, leads, product research)

**What Jackson does:** runs one of three ready-made Apify scrapers and keeps every result with the link it came from.

| Kind | What you get | Apify price |
|---|---|---|
| `search` | Google results for your terms: title, link, snippet, rank | from $1.80 per 1,000 result pages |
| `trends` | Google Trends interest for your terms over the last 3 months | from $3.00 per 1,000 results |
| `places` | local businesses from Google Maps: name, category, address, website, phone, rating | from $1.50 per 1,000 places |

- **Cost cap first:**
  - Every run has a hard cap that Apify can't go over (`maxTotalChargeUsd`).
  - Jackson shows the cap and waits for your approval in **Approvals**. Inside an active Launch, its budget counts as the approval.
  - The smallest cap is $0.05.
- **Leads (`places`):** each business becomes a CRM lead with:
  - source "public business listing"
  - the Google Maps link as proof
  - lawful basis "legitimate interest"

  These are business numbers and websites only, never personal data. Nobody is contacted: outreach still follows the existing email rules and opt-outs.
- **Ask with** `POST /tenants/{t}/research/web`, sending `{kind, terms, location (for places), items}`.

### Click steps
1. Sign in at **console.apify.com** → **Settings** → **API & Integrations** → **Personal API tokens** → **Create a new token**.
2. Name it "Jackson". Leave the permissions at the default, or limit them to running Actors. Copy the token; it's shown only once.
3. In Railway, add `APIFY_TOKEN` and **Seal** it.
4. After the redeploy: **Connections → Web research → Test**. It shows your Apify account and plan.
5. Optional: in Apify, set a **monthly usage limit** under **Billing** as a second safety net.

### Variables
| Name | What to paste |
|---|---|
| `APIFY_TOKEN` | the personal API token (secret, seal it) |

---

## Discord and Telegram (member access from Whop, announcements)

**What Jackson does:**
- **Discord role:**
  - Anyone Whop says has access (active, trialing or completed) and who linked Discord on Whop gets your member role.
  - Anyone whose membership ended loses it.
  - It runs after every Whop sync, or on demand (`POST /tenants/{t}/community/sync`). It only touches members whose access changed.
- **Telegram:**
  - Members who lose access are removed from your group. They can rejoin if they pay again.
  - A paying member gets a one-person invite link that expires in 48 hours (`POST /tenants/{t}/community/telegram/invite`).
- **Announcements** (`POST /tenants/{t}/community/announce`):
  - Checked for income promises and fake claims first.
  - Then each one waits for your approval in **Approvals**. A Launch whose social accounts include `discord` or `telegram` also counts.
- Roles only ever come from real Whop memberships.

### Discord click steps
1. Go to **discord.com/developers/applications** → **New Application** → name it "Jackson".
2. Open the **Bot** tab → **Reset Token** → copy it. That's `DISCORD_BOT_TOKEN`; seal it in Railway.
3. Open **OAuth2** → **URL Generator**:
   - Tick **bot**.
   - Under permissions, tick **Manage Roles**, **View Channels** and **Send Messages**.
   - Open the generated link and add the bot to your server.
4. In your server: **Server Settings** → **Roles** → drag the **Jackson** role **above** your member role. A bot can only give roles below its own.
5. Copy the ids:
   - Turn on Discord **User Settings** → **Advanced** → **Developer Mode**.
   - Right-click the server → **Copy Server ID** → `DISCORD_GUILD_ID`.
   - Right-click the member role → **Copy Role ID** → `DISCORD_MEMBER_ROLE_ID`.
   - Right-click your announcements channel → **Copy Channel ID** → `DISCORD_ANNOUNCE_CHANNEL_ID`.
6. **Connections → Discord → Test**. It shows the server, the member count and the role's name.

### Telegram click steps
1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts. Copy the token it gives you: that's `TELEGRAM_BOT_TOKEN`. Seal it.
2. Add the bot to your group, then make it an **admin** with **Ban users** and **Invite users via link**.
3. Get the group's id:
   - Post any message in the group.
   - Open `https://api.telegram.org/bot<token>/getUpdates` in your browser.
   - Copy `chat.id`. It starts with `-100`. That's `TELEGRAM_CHAT_ID`.
4. **Connections → Telegram → Test**. It shows the bot and the group name.

### Variables
| Name | What to paste |
|---|---|
| `DISCORD_BOT_TOKEN` | the bot token (secret, seal it) |
| `DISCORD_GUILD_ID` | your server id |
| `DISCORD_MEMBER_ROLE_ID` | the role paying members get |
| `DISCORD_ANNOUNCE_CHANNEL_ID` | optional: where announcements go |
| `TELEGRAM_BOT_TOKEN` | the BotFather token (secret, seal it) |
| `TELEGRAM_CHAT_ID` | your group's id (starts with -100) |

---

## Website hosting via Netlify (sites and student platforms)

**Why Netlify:**
- Jackson's sites and course pages are plain HTML, and Netlify publishes that in one call.
- Every site gets HTTPS straight away, and connecting your own domain is a single setting.
- The free plan has a hard limit and never bills you by surprise.
- Cloudflare Pages needs its command-line tool for uploads. Vercel adds a build step these pages don't need.

**What Jackson does:**
- **Websites:** when you approve a site launch, the tested version is published to its own Netlify site on your domain. The previous version is kept so you can roll back.
- **Courses:**
  - `POST /tenants/{t}/courses/{id}/publish` publishes two sites: the student platform (`…-learn.netlify.app`) and the sales page.
  - The sales page shows the real Whop **Enroll now** link if you linked a plan.
  - Only a course that passed every check and was written by the real model can be published.
- **Always approved first:** every publish waits in **Approvals**. The one exception is a Launch you approved with site publishing turned on (`channels.site_publish`).
- **Limits on the free plan:**
  - 3 deploys a minute, 100 a day.
  - If the account runs out of monthly credits, every site on it pauses until the next month.

### Click steps
1. Sign up or sign in at **app.netlify.com** (free plan is fine).
2. Click your avatar → **User settings** → **Applications** → **Personal access tokens** → **New access token**.
3. Name it "Jackson", pick an expiry (or "No expiration"), click **Generate token**, and copy it. It's shown only once.
4. In Railway, add `NETLIFY_AUTH_TOKEN` and **Seal** it.
5. After the redeploy: **Connections → Website hosting → Test**. It shows your Netlify account.
6. **Your own domain:** when a site launches on `yourdomain.com`, Netlify sets that as the site's domain. At your domain registrar, point the domain to Netlify. Netlify's **Domain management** page for the site shows the exact DNS records to add.

### Variables
| Name | What to paste |
|---|---|
| `NETLIFY_AUTH_TOKEN` | the personal access token (secret, seal it) |
| `NETLIFY_SITE_PREFIX` | optional: the start of every Netlify site name (default `jackson`) |

---

## Real-estate data via BatchData (comps and skip tracing, research only)

**Why BatchData, not PropStream:** PropStream has no public developer API. BatchData publishes REST endpoints for comps, property lookups and skip tracing, all with one token.

**What Jackson does:**
- **Comps** (`POST /tenants/{t}/realestate/comps` with an address):
  - Pulls comparable properties within 1 mile (you can choose 0.25 to 5) with similar beds, size and year built.
  - Saves them with the median sale price and price per square foot. The medians use only comps that actually have those numbers.
  - The cost (records × your price) waits for your approval.
- **Skip tracing** (`POST /tenants/{t}/realestate/skip-trace` with one address):
  - Finds the owner's phone numbers and emails.
  - It's personal data, so you approve it twice: once for the personal data, once for its cost.
  - Numbers on the Do-Not-Call list and litigator matches are flagged and never counted as callable.
- **Research only:** nothing contacts an owner, makes an offer or sends a contract.

### Click steps
1. Sign in to **app.batchdata.com**, then left menu → **API Tokens** (key icon) → **Server Side Tokens** → **Add New Token**. Name it "Jackson" and copy it.
2. In Railway, add `BATCHDATA_API_TOKEN` and **Seal** it.
3. Find your plan's price per record on BatchData's billing page. Add it in **cents** as `BATCHDATA_CENTS_PER_RECORD`: for example, `3` for $0.03. Jackson uses it to show the cost before every lookup.
4. **Connections → Real-estate data → Test.** BatchData has no free test call, so this only checks that both values are set. Your first approved comps search is the real test.

### Variables
| Name | What to paste |
|---|---|
| `BATCHDATA_API_TOKEN` | the server-side token (secret, seal it) |
| `BATCHDATA_CENTS_PER_RECORD` | your plan's price per record, in cents |

---

## Trading: Alpaca PAPER account and market data (APEX-7)

**What Jackson does:**
- **Snapshot** (`POST /tenants/{t}/trading/paper/snapshot`):
  - Reads your Alpaca **paper** account: equity, cash, today's P&L, positions and last month's equity curve.
  - Also reads 30 days of daily bars for the symbols you watch.
  - Trading's KPIs show the real **Paper P&L** and **Paper equity** from the latest snapshot. They are never estimated.
- **Paper orders** (`POST /tenants/{t}/trading/paper/orders`): market orders on the paper account only. Jackson's Alpaca connector has no way to reach a live account: the paper address is built in.
- **MNQ and other futures:** Alpaca doesn't offer futures, neither trading nor data. Jackson tests APEX-7's MNQ ideas on **QQQ** (the Nasdaq-100 ETF) and says so on every result.
  - For real MNQ paper trading, a futures broker (Tradovate, NinjaTrader or Interactive Brokers) would be a separate connector. Say which one you use.
- **Live orders stay refused.** That needs a separate live broker connector with daily loss caps, a kill switch and your approval on every order.

### Click steps
1. Sign in at **app.alpaca.markets**. Top left, switch to your **Paper** account (the account number starts with PA). To start fresh, choose **Open New Paper Account** ($100k of paper money by default).
2. On the paper dashboard, find **API Keys** on the right → **Generate New Keys**. Copy both the **Key ID** and the **Secret Key**; the secret is shown only once.
   - These must be **paper** keys. Live keys won't work: Jackson only talks to the paper API.
3. In Railway, add `ALPACA_PAPER_KEY_ID` and `ALPACA_PAPER_SECRET_KEY`. **Seal** both.
4. **Connections → Trading: Alpaca PAPER → Test**. It shows your paper account number and equity.

### Variables
| Name | What to paste |
|---|---|
| `ALPACA_PAPER_KEY_ID` | the paper account's Key ID |
| `ALPACA_PAPER_SECRET_KEY` | the paper account's Secret Key (secret, seal it) |
