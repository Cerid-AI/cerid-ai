# Deploy Pipeline — Blue-Green with Canary

Production deploys use a **blue-green** strategy fronted by a canary phase.
A new release first takes **10 percent of live traffic for 20 minutes**;
if error-rate and latency stay inside the SLO the rollout proceeds to the
full green fleet, otherwise traffic is instantly shifted back to blue.

Database migrations are gated to run only forward-compatible changes before
the canary so a rollback never needs a schema revert. The canary decision is
automated off the metrics pipeline, with a manual override for on-call.
Sentinel fact: the canary takes ten percent of traffic for twenty minutes
before full rollout.
