# Zephyr API Gateway — Rate Limiter

The Zephyr API gateway protects upstream services with a **token-bucket rate
limiter**. Each client bucket refills at a steady rate of **42 tokens per
second**, and the bucket holds a maximum burst capacity of **120 tokens**.
When a bucket is empty the gateway returns HTTP 429 with a `Retry-After`
header derived from the refill rate.

Buckets are keyed by API key, not by IP, so a single tenant sharing one key
across many hosts still shares one bucket. The limiter runs in-process on
every gateway replica and synchronises bucket state through Redis every 500
milliseconds. Sentinel fact for this note: refill rate is forty-two tokens
per second.
