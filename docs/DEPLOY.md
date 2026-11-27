# Putting Jackson OS online (Railway)

This guide is for the owner. There's no coding: you click, copy and paste. It takes about 30–45 minutes the first time.

## Why Railway
- **One place for everything:** Jackson's app, its background worker, its database and the console all run in one Railway project, and the code is already set up for it (`Dockerfile`, `railway.json`).
- **Cheap for one owner:** Hobby is $5/month, which includes $5 of usage. Jackson's expected bill is about **$10–15/month** for hosting (details below).
- **Hands-off updates:** it builds straight from GitHub, and each change merged into `main` redeploys automatically.

> Prices and screens were checked against Railway's and Clerk's websites on 2026-11-26. Button names can drift. If something looks different, look for the closest match.

---

## What you'll have at the end
- `https://<something>.up.railway.app/console` is your owner console. You sign in with Clerk.
- Behind it:
  - **Jackson app + worker:** one service, built from this repo, with a private disk for files.
  - **Postgres 16:** a second service, Railway's database.
- Everything external (Shopify, posting, email, video) stays a **clearly labeled stand-in** until you add that service's key.

---

## Step 1 — Create a Clerk app (sign-in), 5 minutes
1. Go to **clerk.com** → **Sign up**. It's free for up to 50,000 monthly users.
2. **Create application**. Name it "Jackson OS" and keep **Email** as the sign-in method.
3. Open **API keys** in the left menu. Copy the **Publishable key** (it starts with `pk_test_` or `pk_live_`) and keep it handy.
   - You **don't** need the Secret key. Jackson checks sign-ins using Clerk's public key list.
4. Leave it as a **Development** instance for now. It works with the Railway web address. Switch to a Production instance later if you add your own domain; Clerk asks for that.

## Step 2 — Create the Railway project, 10 minutes
1. Go to **railway.com** → **Sign up** and choose **Sign in with GitHub** (use the GitHub account that owns `STEADYLITT/Jaxon-core`).
2. Choose the **Hobby** plan ($5/month).
3. **New Project** → **Deploy from GitHub repo** → pick **STEADYLITT/Jaxon-core**. If Railway asks for access, allow it for that repository.
   - Railway finds the `Dockerfile` and `railway.json` on its own. Leave the root directory as the repo root.
   - The first build takes about 5 minutes and **will fail to start** until Steps 3–5 are done. That's expected.
4. In the same project, click **+ New** → **Database** → **PostgreSQL**. Railway adds a "Postgres" service (version 16).

## Step 3 — Give Jackson a private disk, 2 minutes
1. Click the **Jaxon-core** service (not Postgres).
2. Add a **Volume** (right-click the service, or **Settings → Volumes**). Set the **mount path** to exactly `/data`.
   - This disk keeps Jackson's files (reports, screenshots) and the passwords it generates for itself. Hobby volumes start at 5 GB.

## Step 4 — Set the variables, 5 minutes
In the **Jaxon-core** service, open **Variables** and add these. Type each **name** exactly as shown.

| Name | What to put | What it's for |
|---|---|---|
| `MIGRATION_DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (type it exactly like this; Railway fills in the real value) | Lets Jackson set up its database. Jackson then creates its own **limited** database logins for everyday work. |
| `CLERK_PUBLISHABLE_KEY` | the `pk_...` key from Step 1 | Sign-in for the console. Jackson works out the rest of the Clerk settings from it. |
| `MODEL_MONTHLY_BUDGET_USD` | `20` (or your limit) | Hard monthly cap on AI spending. |
| `ANTHROPIC_API_KEY` | your Claude key (optional on day one; see below) | Lets Jackson think with Claude. Without it, every AI step is a labeled stand-in. |

Then **Settings → Networking → Generate Domain**. Railway gives you a `https://…up.railway.app` address and tells Jackson about it automatically.

**Seal** the `ANTHROPIC_API_KEY` variable (⋮ menu → **Seal**) so its value can never be shown again, even to you.

### Variables Jackson sets for itself (don't add these)
Jackson works these out at start-up. If you set one yourself, yours wins.
- `DATABASE_URL`, `WORKER_DATABASE_URL`: the limited logins for the app and the worker. Their passwords are generated once and kept on the `/data` disk.
- `AUTH_ISSUER`, `AUTH_JWKS_URL`, `AUTH_AUTHORIZED_PARTIES`: worked out from the Clerk key and your web address.
- `PUBLIC_BASE_URL`: your Railway web address.
- `UNSUBSCRIBE_SECRET`: signs the unsubscribe links in emails; generated once.
- `ARTIFACT_DIR`: `/data/artifacts`.
- `APP_ENV=production`: built into the image. It turns on the safety checks, and Jackson **refuses to start** if anything is unsafe.

## Step 5 — First start, then make yourself the owner, 5 minutes
1. Click **Deploy** (or **Redeploy**) on the Jaxon-core service. In **Deploy Logs** you should see these lines, in this order:
   - `schema: up to date`
   - `database roles: jackson_app and jackson_worker can log in`
   - `10/10 checks passed`
   - a line saying the server is running
2. Open `https://<your-address>/console` and **sign up with your email** in the Clerk box.
3. The console says *"You're signed in, but this Jackson OS isn't set up for you yet"* and shows an ID that starts with `user_`. You can also find it in Clerk under **Users** → your name → **User ID**.
4. Back in Railway → Jaxon-core → **Variables**, add:
   - `OWNER_EMAIL`: your email
   - `OWNER_AUTH_SUBJECT`: that `user_...` ID
5. Railway redeploys. The logs show `owner: ready`. Reload the console and you'll see your dashboard, with your five businesses already set up.

## Step 6 — Check it
- `https://<your-address>/health` shows `"status":"ok"`.
- `https://<your-address>/ready` shows `"auth":"configured","database":"ok"`.
- In the console, **Connectors** lists every outside service as **MOCK** except the ones you've added keys for.
- Press **Global stop**, then **Resume work**, so you know it works.

If the service won't start, open **Deploy Logs**. A `FAIL` line names the missing setting, for example `FAIL  auth_configured`: add the Clerk key.

---

## Where to get each key (names only, never paste keys into chat)
| Name | Where it comes from | Needed for | Phase |
|---|---|---|---|
| `CLERK_PUBLISHABLE_KEY` | Clerk dashboard → **API keys** | signing in (required) | 03, 19 |
| `ANTHROPIC_API_KEY` | console.anthropic.com → **Settings → API keys** (add billing there) | research, copy, content drafts, agents | 08 (used by 12–18) |
| `MODEL_MONTHLY_BUDGET_USD` | you choose (default 20) | AI spending cap | 08 |
| `OWNER_EMAIL`, `OWNER_AUTH_SUBJECT` | your email; Clerk → **Users** → User ID | making you the owner | 03 |
| `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_CLIENT_ID`, `SHOPIFY_CLIENT_SECRET` | your store + a Dev Dashboard app created from the store admin: see [`CONNECTORS.md`](CONNECTORS.md) | real draft products (drafts only) | 15 |
| `UPLOADPOST_API_KEY`, `UPLOADPOST_USER` | upload-post.com → **API Keys** / **Manage Users**: see [`CONNECTORS.md`](CONNECTORS.md) | posting approved content | 16 |
| `RESEND_API_KEY`, `RESEND_FROM`, `RESEND_WEBHOOK_SECRET` | resend.com → **API Keys** / **Webhooks**: see [`CONNECTORS.md`](CONNECTORS.md) | sending approved email, bounce handling | 18 |
| `SITE_DOMAIN_VERIFY` | set to `dns` when you launch a website on your own domain | real domain checks | 17 |
| `CLERK_SECRET_KEY`, `S3_*`, `OPENAI_API_KEY` | **not needed now** | — | — |

The posting, email and video connectors are built as labeled stand-ins. When you have those accounts (list below), give me the key **through Railway's Variables page, not in chat**. I'll check the provider's current docs and switch that connector on behind the existing approval gates.

---

## Monthly cost (expected)
| Item | Expected | Notes |
|---|---|---|
| Railway Hobby | **$5** | includes $5 of usage |
| Railway usage above $5 | **about $5–10** | Railway bills RAM at about $10 per GB-month, CPU at about $20 per vCPU-month and disk at about $0.15 per GB-month, by the second. Jackson + worker (~0.6 GB, mostly idle) plus Postgres (~0.2 GB) comes to roughly $10 of usage. |
| Clerk | **$0** | free up to 50,000 monthly users |
| Claude (Anthropic) | **up to your cap**, default **$20** | Jackson stops AI calls at `MODEL_MONTHLY_BUDGET_USD`. |
| **Total** | **about $15/month + AI (capped)** | about $35/month at the default AI cap |

The costs that grow with real launches are listed next.

---

## Accounts for the first real launch, biggest money impact first
| # | Account | What it does in Jackson | Phase | Typical cost (checked 2026-11-26) | Status in the code |
|---|---|---|---|---|---|
| 1 | **AI video for UGC** (e.g. fal.ai with Veo or Kling) | short product/UGC videos for the content pipeline | 16 | pay per second of video: about $0.10–0.40/s, so a 5-second clip is roughly $0.50–2. 50 clips a month is about $25–100. | connector **not built yet**; content drafts are text today. Needs `FAL_API_KEY` plus a small build. |
| 2 | **Shopify store** | draft products now; a real store when you sell | 15 | **development store: free** (Shopify Partners). Basic plan when you go live: about $29–39/month plus card fees (~2.9% + 30¢). | **built**: drafts and read-back are tested. Add the token to turn it on. |
| 3 | **Social posting** (Upload-Post) | posts approved content to your accounts | 16 | free: 10 uploads/month. Basic: about $16–24/month for unlimited uploads across 5 profiles. | stand-in now. Wiring needs the key plus a docs check. |
| 4 | **Claude** (Anthropic) | the thinking: research, copy, drafts | 08 | capped by you (default $20/month) | **built**: switches on with the key. |
| 5 | **Railway** | hosting | go-live | about $10–15/month | **ready** (this guide) |
| 6 | **Email sending** (e.g. Resend) | outreach and notifications | 18 | free: 3,000/month (100/day). Pro: $20/month for 50,000. | stand-in now. Needs the key, a sending domain and a docs check. |
| 7 | **Clerk** | sign-in | 03, 19 | free | **built** |

**Order to do it in:**
1. Clerk + Railway + Claude: go online.
2. Shopify development store: real drafts, no money at risk.
3. Upload-Post free tier: real posting, with your approval on every post.
4. Email.
5. Video, once you've seen the text pipeline work.

---

## Updates, backups, and if something goes wrong
- **Updates:** merging into `main` on GitHub redeploys automatically. Each start re-checks the database and the safety checklist.
- **Backups:** follow `docs/runbooks/backup-restore.md`. Until we automate it on Railway, ask me and I'll set up a scheduled backup that also verifies the restore.
- **Emergency:** press **Global stop** in the console. See `docs/runbooks/incident-global-stop.md`.
- **First real business test:** `docs/runbooks/pilot.md`.
