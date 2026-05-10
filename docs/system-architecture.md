# V6 System Architecture

V6 is the only active kernel and control plane in this repository. New version rollouts are hard cuts: retired kernels, retired API routes, retired Docker entrypoints, retired tests, and retired docs are removed instead of kept as compatibility layers.

## Kernel

- Mission state is event-driven and projected into runs, jobs, waves, packages, artifacts, recovery traces, and provider health.
- AI execution is split into small contract-bound phases for requirements, architecture surface, layout, contracts, package scope, package waves, implementation, QA, security, integration, review, repair, and release.
- Scale profiles change orchestration intensity, not kernel capability.

## Control Plane

- Public control APIs live under `/api/v6/*`.
- System check enforces a single active version policy.
- Storage lifecycle treats runtime workspaces as managed cache with dry-run garbage collection and export receipts.

## Rollout Policy

- The active version is the version in `VERSION`.
- A new active version must migrate or delete retired version artifacts before it is considered ready.
- Historical evidence belongs outside the active repository or inside external backups, not in product code, tests, Docker files, or current docs.
