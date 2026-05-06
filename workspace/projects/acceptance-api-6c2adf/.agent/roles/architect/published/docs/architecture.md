# Architecture

## Product
Acceptance api-platform

## Shape
- `apps/web`: browser UI
- `apps/api`: backend services
- `tests`: verification assets
- `infra`: local startup and deployment helpers

## Key Modules
- identity-and-service-auth
- public-api-surface
- internal-service-layer
- integration-adapters
- contract-testing
- release-and-runtime-ops

## Suggested evolution
- Introduce typed API contracts
- Add database and auth providers
- Split services by bounded context once modules stabilize
