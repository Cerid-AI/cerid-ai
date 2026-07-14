# Zephyr Client — Retry & Backoff Policy

The Zephyr client SDK retries failed requests using **exponential backoff**.
The base delay is **250 milliseconds**, doubling on each attempt, with full
jitter applied so a thundering herd cannot re-synchronise. The client gives
up after **5 retries** and surfaces the last upstream error to the caller.

Only idempotent verbs (GET, PUT, DELETE) are retried automatically; POST is
retried only when the server returns an explicit `Idempotency-Replayable`
header. This note is deliberately close in vocabulary to the gateway rate
limiter (both mention requests, rate, and the Zephyr service) but the
retriable fact here is the backoff base delay of two hundred fifty
milliseconds and the maximum of five retries.
