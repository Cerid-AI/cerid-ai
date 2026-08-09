# Risk Register Practices — Likelihood × Impact, Mitigation Strategies

A risk register is a structured catalogue of project risks with assessment, ownership, and response plans. Done well, it's the project manager's most useful early-warning system. Done poorly, it's a compliance artifact nobody reads.

## Identifying risks

Risks come from many sources. A productive identification session draws from:
- **Lessons learned** from prior similar projects — the same risks tend to recur
- **Stakeholder interviews** — sales knows the customer-side risks; engineering knows the technical ones
- **External factors** — regulatory changes, vendor stability, market timing
- **Assumption inversion** — for each project assumption, ask "what if it's wrong?"

Cast a wide net during identification, then filter. A first-pass risk register with 100 items is healthier than one with 10 — pruning is easier than re-discovery.

## Likelihood × Impact assessment

The classic 5×5 matrix scores each risk on:

| Likelihood | Definition |
|---|---|
| 1 - Rare | <10% chance during project |
| 2 - Unlikely | 10-30% |
| 3 - Possible | 30-60% |
| 4 - Likely | 60-90% |
| 5 - Almost certain | >90% |

| Impact | Definition (per project) |
|---|---|
| 1 - Negligible | <1% schedule or budget hit |
| 2 - Minor | 1-5% |
| 3 - Moderate | 5-15% |
| 4 - Major | 15-30% |
| 5 - Severe | >30% or scope-killing |

Risk score = likelihood × impact, plotted on a heat map. Risks in the high-likelihood/high-impact quadrant get immediate attention; the low/low quadrant gets accept-and-move-on.

Define impact thresholds project-specifically — a $5M project's "moderate" is a $10M project's "minor". Generic templates lead to generic responses.

## Response strategies

For each significant risk, the response is one of four:

1. **Avoid** — restructure the project to remove the risk entirely. Drop a feature, change vendor, decouple a dependency. Highest cost, but eliminates the risk.

2. **Mitigate** — reduce likelihood or impact. Common technical examples: adding test coverage to reduce regression likelihood, building a rollback path to reduce deployment impact.

3. **Transfer** — shift the risk to a third party. Insurance, fixed-price contracts, vendor SLAs. The risk doesn't disappear; the financial consequence shifts.

4. **Accept** — explicitly decide to do nothing, with a contingency plan if the risk materializes. Appropriate for low-probability or low-impact risks where mitigation cost exceeds expected loss.

Document the chosen strategy AND the trigger conditions: "If the vendor misses milestone X by Y days, switch to alternative Z." Pre-decided responses execute faster under pressure.

## Ownership

Every risk needs a single owner — the person responsible for monitoring the trigger conditions and executing the response. Distributed ownership ("the team will watch for this") means no one watches.

The owner isn't necessarily the person who'd execute the response — they're the watcher. Their job is to surface the risk to the right decision-maker when the trigger fires.

## Cadence

Review the risk register at a regular cadence proportional to project length. Weekly for a 6-week project; monthly for a 12-month project. Each review:
- Re-score: are likelihoods or impacts shifting?
- Close: are any risks now resolved or moot?
- Add: are new risks emerging?
- Verify ownership: do all open risks still have an active owner?

Stale registers — last touched at kickoff and never since — are common and useless. Currency is more important than comprehensiveness.

## Anti-patterns

- **Risk theater**: an exhaustive register that never drives a decision. The test is simple — has any item in the register changed a project decision in the last quarter?
- **Score inflation**: rating everything 4 or 5 on impact "to be safe". Loses the ability to prioritize.
- **Mitigation without measurement**: planning a response without defining what success looks like.
- **Aggregating risks into themes**: "process risks" or "technical risks" as single line items. Hides the specific actionable concerns.
