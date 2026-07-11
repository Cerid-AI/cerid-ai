# Project Estimation — Story Points, Three-Point Estimates

Software estimates are wrong; the question is how systematically you can be wrong and still deliver something useful. Two estimation techniques sit at opposite ends of the formality spectrum and both have their place.

## Story points

Story points abstract away the calendar. A team estimates each backlog item as 1, 2, 3, 5, 8, 13, or 20 points (Fibonacci, deliberately spaced to discourage false precision at higher numbers). The number reflects relative effort: complexity, uncertainty, and amount of work — not hours.

The team converts points to schedule via **velocity** — the points completed per sprint, averaged over recent sprints. If velocity is 30 points/sprint and the upcoming feature is 90 points of work, plan for 3 sprints.

Story points work because:
- Relative comparison is a more reliable human skill than absolute time prediction
- Velocity self-corrects for team-specific factors (skill, tooling, process overhead)
- Decoupling from hours discourages micro-management of how engineers spend their time

Story points fail when:
- Used for cross-team comparison ("Team A delivers 50 points, Team B delivers 30")
- Used for individual performance reviews
- Inflated to look productive, eroding the relative-comparison signal
- Velocity is calculated on closed tickets only, ignoring scope cuts and incomplete work

Planning poker — team members independently estimate, then reveal and discuss disagreements — is the standard technique for collective estimation.

## Three-point estimates (PERT)

For high-stakes individual estimates, the three-point technique requests:
- **O** — optimistic estimate (best case if everything goes right)
- **L** — most likely estimate (the realistic single-number you'd give)
- **P** — pessimistic estimate (worst case short of catastrophic failure)

The PERT-weighted estimate is:

    Expected = (O + 4L + P) / 6

The variance is:

    Variance = ((P - O) / 6)²

The expected value gives a more honest single number than the most-likely alone, because it weights tail-risk into the estimate. The variance quantifies how much you should trust it.

For a 6-week project with O=4, L=6, P=12, the expected duration is (4 + 24 + 12) / 6 = 6.7 weeks, with standard deviation of ~1.3 weeks. The 90th-percentile duration (rough heuristic: expected + 1.3 standard deviations) is about 8.3 weeks. This is your buffer-aware commitment.

Three-point works for:
- Individual large estimates (a quarter of work, a contractor bid)
- Risk-sensitive contexts (regulatory deadlines, customer commitments)
- Surfacing implicit assumptions — discussing the gap between O and P forces explicit discussion of what could go wrong

## Why estimates fail

The dominant reasons estimates miss:

1. **Forgetting the long tail of small tasks** — testing, code review, documentation, deploy preparation, edge-case handling. Easy to underestimate as overhead, painful in aggregate.

2. **Estimating happy-path only** — assuming first attempt succeeds. Real engineering involves discovery and rework.

3. **Not accounting for context-switching** — an engineer with three concurrent priorities won't deliver three times faster than one focused engineer.

4. **Anchoring on the answer the manager wants to hear** — political distortion downward.

5. **Failing to update estimates as new information arrives** — initial estimate becomes a commitment instead of a hypothesis.

## Hybrid approach

Many teams use story points for sprint-level planning (week-to-week) and three-point estimates for project-level commitments (quarter-to-quarter). Story points handle the routine; three-point handles the strategic.

The most important practice regardless of technique: **track actuals against estimates**. Without comparison data, estimation skill doesn't improve. A simple log of "estimated X, actually Y" maintained per team over a year reveals systematic biases — your team underestimates by 30%, you tend to forget cross-team dependencies, etc. — that no single-project introspection would surface.

Estimation accuracy is mostly about calibration over time, not about cleverer formulas in the moment.
