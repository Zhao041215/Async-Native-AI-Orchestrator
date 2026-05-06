# Architecture

## Product
Acceptance web-saas

## Shape
- `apps/web`: browser UI
- `apps/api`: backend services
- `tests`: verification assets
- `infra`: local startup and deployment helpers

## Key Modules
- tenant-and-identity
- web-dashboard
- billing-and-subscriptions
- application-api
- audit-and-observability
- test-and-release-pipeline

## Suggested evolution
- Introduce typed API contracts
- Add database and auth providers
- Split services by bounded context once modules stabilize
