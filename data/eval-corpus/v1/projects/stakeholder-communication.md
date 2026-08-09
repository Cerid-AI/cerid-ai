# Stakeholder Communication — RACI, Escalation Paths

Project communication fails not because people don't talk, but because they don't agree on who owns what or when to escalate. RACI matrices and explicit escalation paths address those two failure modes directly.

## The RACI matrix

RACI assigns four roles per task or decision:

- **Responsible** — the person who does the work. Multiple people can be Responsible for parts of the work.
- **Accountable** — the single person who owns the outcome and signs off. Exactly one A per row; this is non-negotiable.
- **Consulted** — people whose expertise informs the work; two-way communication before the decision is made.
- **Informed** — people who need to know the outcome; one-way communication after.

A RACI matrix lays tasks down the rows and people (or roles) across the columns:

| Task | PM | Tech Lead | Eng | Designer | Sales | CTO |
|---|---|---|---|---|---|---|
| Architecture decision | I | A | R | C | — | C |
| Feature spec | A | C | C | R | C | I |
| Sales demo | C | I | I | I | A | I |
| Production deploy | I | A | R | — | I | C |

The discipline of filling in the matrix surfaces problems immediately:
- **Multiple A's per row** — accountability is ambiguous; resolve before work begins
- **No A** — the decision is unowned; create the role
- **All I's** — nobody is doing the work; the task isn't real
- **Same person on too many R's** — single point of failure; redistribute

RACI works best at coarse granularity. Mapping every Jira ticket is overhead theater; mapping the dozen consequential decisions of a quarter is high leverage.

## Escalation paths

An escalation path defines who decides when the team can't. The default in many organizations is "escalate to your manager", which fails when:
- The manager doesn't have the cross-functional authority to decide
- The escalation crosses organizational boundaries
- The decision needs speed greater than the manager's response time

A well-defined escalation path documents:
- **Trigger condition** — what specifically warrants escalation? "Slipping > 1 sprint", "blocked by another team for > 3 days", "scope change > X%".
- **First escalation contact** — name a person, not a role; make sure their backup is also named.
- **Response SLA** — how quickly should the contact respond? Hours, days?
- **Subsequent contact** — if no response within SLA, escalate to whom?
- **Decision authority** — at each level, what is the contact authorized to decide vs needing to consult further?

Document this in the project charter and in the team's runbook. Don't make team members guess in the moment of a fire.

## Communication cadence

Different stakeholders need different cadences:

- **Sponsor** — typically 30 minutes per month, focused on goals, risks, and resource needs. Concise dashboard with KPIs and exceptions.
- **Direct stakeholders** — weekly written status (what shipped, what's next, what's blocked) and biweekly meetings.
- **Cross-functional partners** — embedded standups or weekly checkpoints depending on dependency frequency.
- **Wider organization** — quarterly review or all-hands update.

The pattern is: more frequent for higher dependency, less frequent for lower dependency. Inverting this — sending the sponsor weekly minutiae or only telling cross-functional partners about you at quarterly reviews — wastes both audiences' time.

## Bad-news reporting

The hardest stakeholder communication is delivering unwelcome information. The principle is **fast, factual, with options**:

- **Fast** — the worst time to deliver bad news is right before a milestone. The earlier the warning, the more options remain.
- **Factual** — what specifically is true. Timeline, scope, cost, blocker. No softening that obscures the issue.
- **With options** — present the menu of responses with trade-offs, not just the problem. "We're 3 weeks behind; we can ship on time by cutting features X and Y, ship late with everything, or add 2 engineers and ship on time."

Stakeholders almost always handle bad news better than they handle being surprised by it. The rule of thumb: if you're sitting on bad news for "the right time to share it", that time was a week ago.

## Common pitfalls

- **Status reports nobody reads** — too long, too rote, sent into a black hole. Test with: when did the recipient last act on something in the report?
- **Decision logs that vanish** — important calls made in Slack threads, lost to history. Capture in the project's central doc.
- **Meeting-driven communication** — everything requires a meeting. Async written updates scale better.
- **Hiding context from new hires** — RACI and escalation docs should be onboarding-day reading, not tribal knowledge.
