# Project Nimbus — Ledger Datastore Decision

For the Nimbus ledger service we **chose PostgreSQL over DynamoDB**. The
deciding factor was **strong transactional guarantees**: the ledger needs
multi-row ACID transactions for double-entry bookkeeping, and DynamoDB's
eventual-consistency model would have forced application-level compensation
logic and opened a class of reconciliation bugs.

We accepted that Postgres requires more operational care (connection
pooling, vacuum tuning) in exchange for correctness. A read replica handles
reporting queries so they never contend with the ledger write path. Sentinel
fact: Nimbus uses Postgres, not DynamoDB, for the ledger.
