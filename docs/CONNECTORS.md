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
| — | **Claude** | `ANTHROPIC_API_KEY` (already set) | how many models your key can use (free call) |

Never needed yet: website hosting and SMS/calls aren't built, so they always show MOCK. File storage (S3) is optional, because files live on the Railway disk.

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
