# Agile vs Waterfall — Sprint Cadence, Retrospectives vs Phase Gates

Agile and Waterfall represent two fundamentally different theories of how to coordinate a team toward a shippable product. Most teams today pick one or hybridize, but understanding the contrast clarifies the trade-offs.

## Waterfall

Waterfall is a sequential phase-gate model: requirements → design → implementation → verification → maintenance. Each phase produces a signed-off artifact that becomes the input to the next phase. Backtracking is expensive — once a phase is closed, returning to it requires reopening prior decisions and often renegotiating scope or timeline.

Waterfall works when:
- The problem is well-understood and stable (e.g., porting a known system to new hardware)
- Regulations or contracts require formal documentation at each gate (defense, aerospace, some healthcare)
- The cost of late discovery is catastrophic (firmware burned into ROMs, regulatory submissions)

Waterfall fails when:
- Requirements are uncertain or evolving — gathering them all upfront produces an obsolete spec by implementation time
- Customer feedback is a primary input — phase gates push the customer to the end
- Hardware-software co-design or rapidly changing environments

## Agile (Scrum variant)

Scrum, the most common Agile framework, organizes work into fixed-length **sprints** (typically 1-3 weeks). Each sprint follows a cycle:
- **Sprint planning** — team and product owner agree on what to build this sprint, drawn from the prioritized backlog
- **Daily standup** — 15-minute synchronization on what each person did yesterday, will do today, and any blockers
- **Sprint review** — team demos completed work to stakeholders at sprint end
- **Sprint retrospective** — team meets internally to discuss what went well, what didn't, and one or two improvements to try next sprint

The team commits only to the current sprint. Future work lives in the backlog and is reprioritized continuously. The bet is that small, frequent feedback loops correct course faster than longer phases — and that the cost of small frequent corrections is less than the cost of one large late correction.

Agile works when:
- Requirements emerge through use (most consumer software, internal tools)
- Stakeholders can engage frequently
- The team has authority to make decisions within sprints

Agile fails when:
- Stakeholders are absent — a Product Owner who can't decide leaves the team flailing
- Sprint cadence becomes ritualistic rather than reflective
- "Agile" gets used as cover for "no plan beyond the next two weeks"

## Retrospectives — the engine of improvement

The retrospective is what distinguishes evolved Scrum from cargo-cult Scrum. Without the retrospective, sprints just chunk Waterfall work into smaller boxes. The retrospective is where the team improves *how* it works, not just what it ships.

A productive retrospective:
- Identifies one or two specific changes (not 15) to try next sprint
- Tracks whether the prior sprint's changes actually stuck
- Surfaces interpersonal or process issues without blame
- Ends with concrete commitments, not vague aspirations

A failed retrospective: ritual recitation of "sprint went well, no blockers" with no honest assessment.

## Hybrids in practice

Most mature teams run a hybrid:
- **Strategic phase planning** at the quarter level (more Waterfall-flavored — what business outcomes are we targeting?)
- **Tactical sprint execution** within each quarter (Agile)
- **Continuous integration / continuous delivery** flattens the deployment phase entirely

The labels matter less than the underlying questions: How frequently do we get real feedback from users? How quickly can we change direction when reality contradicts our plan? How do we surface and correct mistakes? Both Agile and Waterfall offer answers; the right one depends on the context.

## Common pitfalls

- **"Mini-Waterfalls inside sprints"** — designing the whole feature in week 1, building in week 2. Lost the iterative benefit.
- **Sprint planning without prioritization** — committing to everything in the backlog, missing the trade-off discussion.
- **Skipping retrospectives when busy** — the sprints when you most need to slow down and reflect.
- **Treating story points as a productivity metric** — story points estimate effort; comparing teams' point velocity creates perverse incentives to inflate estimates.
