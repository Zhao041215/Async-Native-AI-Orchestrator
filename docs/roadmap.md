# Hosted V2 Roadmap

## Current Baseline

- V2-only control plane
- hosted-local tenant and identity boundaries
- persisted approvals, audit events, and worker jobs
- V2-native system and acceptance checks
- V1 runtime removed

## Next Hardening Steps

1. Add a real Redis worker implementation behind the current worker-job contract.
2. Add Postgres storage adapter tests for hosted deployment.
3. Add auth provider integration beyond dev-session headers.
4. Add tenant-level quotas and budget enforcement.
5. Add release-candidate rollback records after apply.
6. Add streaming event delivery for long-running hosted runs.
