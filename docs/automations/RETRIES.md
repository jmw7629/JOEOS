# Retries

Bounded retry policy on every automation definition:

- max_attempts (1..10)
- backoff_seconds, backoff_factor, max_backoff_seconds
- retryable_errors: OLLAMA_UNAVAILABLE, MODEL_TIMEOUT, MODEL_LOADING, OLLAMA_ERROR

Non-retryable: capability denied, agent missing, approval denied, invalid
configuration, unsupported operation. Failed non-retryable runs transition to
`failed` with the error category recorded. Never infinite retry.

On a retryable failure the run transitions to `retry_wait` with `next_retry_at`
computed by bounded exponential backoff; the scheduler reclaims it when due.
