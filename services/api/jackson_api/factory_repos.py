"""App Factory repos (ADR-050): put a tested site version or a course's pages in its own
PRIVATE GitHub repo, in one commit, after the owner approves."""

import uuid

from .worker_errors import NonRetryableError

README = """# {title}

Built by Jackson OS ({what}). Static files, no build step: open `index.html`.

- Source: {source}
- Every page passed Jackson's checks (HTML, accessibility, claims, browser preview).
"""


def push_handler(ctx):
    from .connectors import github_repos as G
    from .models import Course, RunEvent, SiteProject, SiteVersion
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    with ctx.db_session() as db:
        if inp.get("course_id"):
            c = db.get(Course, uuid.UUID(str(inp["course_id"])))
            if c is None or c.tenant_id != ctx.tenant_id:
                raise NonRetryableError("course not found")
            if c.status != "ready" or c.mode == "mock":
                raise NonRetryableError("only a course that passed its checks and was written "
                                        "by the real model can be pushed")
            title, label = c.title, f"course-{c.title[:40]}-{str(c.id)[:6]}"
            files = {**{f"platform/{k}": v for k, v in c.platform_files.items()},
                     **{f"sales/{k}": v for k, v in c.sales_files.items()}}
            what, source = "Course Factory", f"course {c.id}"
        else:
            site = db.get(SiteProject, uuid.UUID(str(inp["site_id"])))
            v = db.get(SiteVersion, uuid.UUID(str(inp["version_id"])))
            if site is None or site.tenant_id != ctx.tenant_id or v is None or \
                    v.site_id != site.id:
                raise NonRetryableError("site version not found")
            if not v.passed:
                raise NonRetryableError(f"version {v.number} did not pass its tests")
            title, label = site.name, f"site-{site.name[:40]}-{str(site.id)[:6]}"
            files, what = dict(v.files), "Website Factory"
            source = f"site {site.id} version {v.number}"
        db.rollback()
    name = G.repo_name(label)
    api = G.client()
    ctx.authorize(Action(Category.PRODUCTION_CHANGE,
                         f"push '{title[:80]}' to the private GitHub repo {api.owner}/{name}",
                         channel="github"))
    repo = api.ensure_repo(name, f"{what}: {title}"[:300])
    files["README.md"] = README.format(title=title, what=what, source=source)
    res = api.commit_files(name, files, f"Jackson: {source}")
    out = {"repo": f"{api.owner}/{name}", "url": repo.get("html_url") or
           f"https://github.com/{api.owner}/{name}", "private": True, "files": len(files),
           "commit": res["commit"], "source": source}
    with ctx.db_session() as db:
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="repo.pushed",
                        data=out))
        db.commit()
    out["summary"] = f"Pushed {len(files)} files to {out['repo']} (private)"
    return TaskResult(out)
