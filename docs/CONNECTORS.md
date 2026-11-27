# Connecting Jackson to your accounts

Each connector stays a clearly labeled stand-in (MOCK in the console's **Connectors** panel) until its variables are set in **Railway → Jaxon-core → Variables**. Paste keys only there, never into chat. After saving, Railway redeploys and the panel shows **LIVE**.

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
9. Railway redeploys. In the logs: `connector: shopify.drafts enabled for the owner's workspace (drafts only)`. In the console, **Connectors → Shopify drafts: LIVE**.

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

### Variables
| Name | What to paste |
|---|---|
| `UPLOADPOST_API_KEY` | your Upload-Post API key. Seal it in Railway. |
| `UPLOADPOST_USER` | the Upload-Post **profile name** your accounts are connected under |

**In a Launch approval,** list the social accounts by platform name: `instagram`, `tiktok`, `x`, `facebook`, `youtube_shorts`. Instagram, TikTok and YouTube need an image or video. Text-only posts go to X and Facebook.
