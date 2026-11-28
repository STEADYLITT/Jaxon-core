# ADR-050: App Factory builds go to private GitHub repos

**Status:** accepted, 2026-11-28

## Decision
**API:** GitHub REST `api.github.com`, version `2022-11-28`, with a fine-grained personal access token (`GITHUB_APP_TOKEN`).
- Test: `GET /user`.
- Repo: `GET /repos/{owner}/{name}` finds an existing one.
  - If it is not there, `POST /user/repos` creates it, or `POST /orgs/{owner}/repos` when the owner isn't the token's user. It is always created with `private: true, auto_init: true`.
  - An existing **public** repo is refused.

**One commit per push (Git Data API):**
1. Read the `heads/main` ref, and the tree of the commit it points to.
2. `POST git/trees` with `base_tree` and every file inlined.
3. `POST git/commits`.
4. `PATCH git/refs/heads/main`.

Limits and checks:
- At most 500 files and 5 MB per push.
- `..`, absolute and `.git/` paths are refused.

**Task `repo_push`** ("Repo Keeper", Factory → Build):
- Sources:
  - a site version that `passed`;
  - or a course that is `ready` and was written by the live model: `platform/…` and `sales/…`, plus a README.
- Every push is a `PRODUCTION_CHANGE` (`channel = github`), so it always needs the owner's approval.
- A `repo.pushed` run event records the repo, URL, commit and file count.

**API routes:**
- `POST /tenants/{t}/factory/repos`
- `GET /tenants/{t}/factory/repos`

**KPI (Factory):** "Builds in private GitHub repos".
