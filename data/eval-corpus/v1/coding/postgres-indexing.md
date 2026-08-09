# PostgreSQL Indexing — B-tree, GIN, BRIN

PostgreSQL offers several index types that suit different access patterns. Choosing the right one for a workload often makes more difference than schema-level optimization.

## B-tree (default)

`CREATE INDEX ... USING btree (col)` is the workhorse and the default when you don't specify a method. B-trees support equality and range queries (`=`, `<`, `<=`, `>`, `>=`, `BETWEEN`) on data with a total ordering. The index stores keys in sorted order and uses balanced-tree traversal to locate values in O(log n) time.

B-trees handle multi-column indexes naturally; the leftmost-prefix rule applies — an index on `(a, b, c)` can serve queries filtering on `a`, `(a, b)`, or `(a, b, c)`, but not `b` alone. Composite indexes are most useful when columns are frequently queried together, especially when the leading column has high selectivity.

Partial indexes (`WHERE active = true`) keep B-trees small when only a subset of rows matter. Expression indexes (`(lower(email))`) support case-insensitive equality.

## GIN (Generalized Inverted Index)

`USING gin` indexes contain a posting list per indexed value — useful when one row has many values to index (e.g., tags) or when querying for membership in composite values. The two dominant uses:

- **`jsonb`** — `gin (data jsonb_path_ops)` accelerates `data @> '{"key": "value"}'` containment queries common in document-style use of jsonb.
- **Full-text search** — `gin (to_tsvector('english', body))` makes `@@` ts_query lookups fast.
- **Array containment** — `gin (tags)` for `tags @> ARRAY['python']`.

GIN is heavier than B-tree on writes — every value in the indexed column inserts an entry. The `fastupdate` GIN parameter buffers updates to amortize the cost.

## BRIN (Block Range Index)

`USING brin (col)` stores summary statistics (min/max) per consecutive block range rather than per row. Tiny on disk — often 1000× smaller than a B-tree — but only useful when the table's physical row order correlates with the indexed column. Time-series and append-only logs are the canonical case: rows arrive in chronological order, and a BRIN on `created_at` lets PostgreSQL skip entire block ranges that don't overlap a date filter.

BRIN suits tables that:
- Have natural physical clustering (often achieved via `CLUSTER` or naturally append-only writes)
- Are very large (millions to billions of rows)
- Are queried with selective range filters

The `pages_per_range` storage parameter trades accuracy for size — smaller ranges give tighter min/max bounds at the cost of more index entries.

## Choosing the right index

| Workload | Index |
|---|---|
| Equality + range on a column | B-tree |
| Composite filters with leftmost-prefix | B-tree multi-column |
| jsonb containment queries | GIN with jsonb_path_ops |
| Full-text search on a body column | GIN with to_tsvector |
| Array membership | GIN |
| Time-series append-only on multi-billion-row tables | BRIN |
| GIS spatial queries | GiST |

Always confirm with `EXPLAIN (ANALYZE, BUFFERS)` — PostgreSQL's planner sometimes picks Seq Scan over an index when the table is small or the filter selectivity is low. A tested plan beats an assumed one.
