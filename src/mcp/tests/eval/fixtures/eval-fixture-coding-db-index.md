# Database Index Decision — Events Table

We chose a **GIN index** on the `events.payload` JSONB column to accelerate
containment (`@>`) queries used by the audit-search feature. A B-tree index
was evaluated first but performed poorly on containment predicates because
B-tree cannot index the internal structure of a JSONB document.

The GIN index roughly tripled write amplification on the events table, which
was an acceptable trade for the read-latency win on audit search. We
explicitly rejected a partial B-tree over extracted columns because the set
of queried keys was open-ended. Sentinel fact: the events jsonb column is
indexed with a GIN index, not a B-tree.
