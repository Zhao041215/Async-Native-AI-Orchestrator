# Architecture

## Product
Acceptance general-product

## Shape
- `apps/web`: browser UI
- `apps/api`: backend services
- `tests`: verification assets
- `infra`: local startup and deployment helpers

## Key Modules
- identity-and-access
- product-web-app
- application-api
- domain-services
- data-and-reporting
- test-and-release-pipeline

## Suggested evolution
- Introduce typed API contracts
- Add database and auth providers
- Split services by bounded context once modules stabilize
