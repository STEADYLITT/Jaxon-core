# ADR-045: Website hosting on Netlify

**Status:** accepted, 2026-11-28

## Decision
Netlify hosts Jackson's sites and course pages.

**Why Netlify:**
- One documented API call deploys plain static HTML (a zip).
- HTTPS on `*.netlify.app` comes immediately.
- A custom domain is one field.
- The free plan is a hard cap that never bills by surprise.

**Alternatives rejected:**
- Cloudflare Pages: raw-API uploads are only documented through Wrangler.
- Vercel: adds a build pipeline we don't need.

**API:** `https://api.netlify.com/api/v1` with `Authorization: Bearer NETLIFY_AUTH_TOKEN`.
- Test: `GET /user`.
- Find a site: `GET /sites?name=` (exact match on the name).
- Create a site: `POST /sites {name, custom_domain}`. A changed domain is updated with `PATCH /sites/{id}`.
- Deploy: `POST /sites/{id}/deploys` with `Content-Type: application/zip`, then poll `GET /deploys/{id}` until the state is `ready`. An `error` state fails the deploy; a timeout is retryable.
- Zips refuse absolute paths and `..` paths.
- Site names come from `site_name()`: `NETLIFY_SITE_PREFIX` (default `jackson`) plus a slug, so a re-publish reuses the same site.

**Site launches:** `sites.launch_handler` now publishes through `connectors.hosting.host()`. That is Netlify when the token is set, otherwise the labeled MOCK `mock://` URL, as before. The approval is unchanged: a `PRODUCTION_CHANGE` with `channel = site_publish`.

**Course publishing** (`POST /tenants/{t}/courses/{id}/publish`, task `course_publish`, the "Course Publisher"):
- Refused if the course is not `ready` or was drafted by the MOCK model.
- Asks for `PRODUCTION_CHANGE` approval.
- Publishes two sites:
  - the student platform: `<slug>-learn`
  - the sales page, with the Whop checkout link swapped in when there is one
- Records a `course.published` event. `GET /courses/{id}` shows it as `published`.

**Launches:** a Launch covers a site or course publish only when its channels say `site_publish: true`.

**KPI (Factory):** "Published on real hosting" counts live sites whose URL is `https://`.

## Without a key
Launches still go through approval and get a `mock://` URL. Courses get `[MOCK]` summaries. Nothing is published.
