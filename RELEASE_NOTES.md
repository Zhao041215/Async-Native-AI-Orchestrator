# V6.4.0 Release Notes

This release hardens the V6-only repository policy.

## What changed

- V6 is the sole active kernel and `/api/v6/*` remains the only supported control surface.
- Architecture execution was split into surface, layout, contracts, and deterministic merge stages.
- Package planning was split into package scope planning and wave planning to avoid a single oversized planner prompt.
- Retired generation documentation was removed instead of being carried forward as compatibility baggage.
- The version marker now reads `v6.4.0-100k-split-planning-single-active-version`.

## Notes

- Provider URLs and model endpoints remain compatible with the configured OpenAI-style provider.
- Runtime workspaces, logs, and artifacts are still managed by the V6 storage lifecycle policies.
