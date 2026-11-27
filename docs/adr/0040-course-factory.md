# ADR-040: Course Factory, the first end-to-end department slice

**Status:** accepted, 2026-11-27

## Decision
"AI trading course is trending, build it" runs the whole Course & Education Factory loop up to Steady's approval, then stops.

1. **Route:** the command bar sends a Courses command that asks to build (build, create, make, launch, turn, write) to the **Course Builder** agent (`course_build`). It lives on the Courses building's "Course Design" floor.
2. **Outline:** one structured model call returns the title, subtitle, audience, promise, outcomes and modules with lessons. It is capped at 4 modules × 3 lessons.
3. **Lessons:** one model call per lesson returns a script, slide cards, a multiple-choice quiz and worksheet prompts.
4. **Student platform draft:** static pages with no scripts:
   - sign-in
   - dashboard, with progress (0 of N)
   - one page per lesson, with its quiz (answers behind "Show the answer")
   - certificate template
   - privacy notice

   Sign-in, saved progress, certificates and payments need Whop (`WHOP_API_KEY`). The pages say so in dashed "Draft" boxes.
5. **Sales page draft:** includes:
   - who it's for, what you'll learn and the curriculum
   - a price only if Steady gave one in the command; it is never invented
   - a Whop checkout placeholder that says nothing can be bought yet
6. **Checks (both sites):**
   - the website factory's HTML and accessibility checks (one h1, labels, links, privacy notice, colour contrast)
   - the claims check: income promises, "get rich", "guaranteed income" and fake reviews all fail it
   - a real Chromium preview at desktop and phone sizes

   A course with any problem is saved as `needs_fixes` and lists the problems.
7. **Launch proposal:** a `launches` row with `status = proposed`, no budget and no channels. The plan text says what was built, what's missing and the suggested channels. Steady sets the budget, cap, channels and kill rules and approves it once. Nothing is published, charged or emailed by this flow.
8. **Memory:** a `project_state` fact about what was drafted, with the run as its source. With no model key, nothing is written: nothing was learned.

## Data
- New table: `courses`. It has RLS. The worker can only insert; the app can only read. A rebuild is a new row.
- API:
  - `GET /tenants/{t}/courses`
  - `GET /tenants/{t}/courses/{id}`
  - `GET /tenants/{t}/courses/{id}/files/{platform|sales}/{page}`: strict CSP, so no scripts run and forms submit nowhere.
  - `POST /tenants/{t}/courses` with `{brief}`: the same build without the command bar.
- City: the Courses building panel lists every course with its lesson count, check results and launch status ("waiting for your approval").

## Without keys
- No `ANTHROPIC_API_KEY`: every step still runs on the MOCK model. The course is saved with `mode = mock`, its titles say `[MOCK]`, and the launch plan says so.
- No `WHOP_API_KEY`: everything is a draft and the pages say so.

## Next
- A Whop connector, which unlocks real checkout, sign-in, progress and certificates.
- A hosting connector, so an approved launch can publish both sites.
- Funnel emails through the existing Resend outreach, under the launch's scope.
