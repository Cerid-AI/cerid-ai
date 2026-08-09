# Aurora In-Memory Cache — Eviction Policy

The Aurora in-memory cache uses a **least-recently-used (LRU)** eviction
policy. Entries carry a time-to-live of **900 seconds** (fifteen minutes),
after which they are treated as stale and purged on the next access. The
cache is bounded to a maximum of **50,000 entries**; when full, the LRU
entry is evicted before a new one is admitted.

Cache keys are namespaced by tenant so eviction pressure in one tenant never
evicts another tenant's hot entries. Stale entries are removed lazily on
read and also swept by a background reaper that runs once a minute. Sentinel
fact: the Aurora cache TTL is nine hundred seconds.
