# Backlog

## Modules
- identity-and-access
- product-web-app
- application-api
- domain-services
- data-and-reporting
- test-and-release-pipeline

## Work Packages
- wp-plan: Repository and requirement decomposition -> planner [planning]
- wp-arch: Cross-subsystem architecture baseline -> architect [architecture]
- wp-ss-01: Implement identity-and-access -> backend [backend]
- wp-ss-02: Implement product-web-app -> frontend [frontend]
- wp-ss-03: Implement application-api -> backend [backend]
- wp-ss-04: Implement domain-services -> backend [backend]
- wp-ss-05: Implement data-and-reporting -> devops [integration]
- wp-ss-06: Implement test-and-release-pipeline -> devops [integration]
- wp-verify: Cross-package verification -> qa [verification]
- wp-review: Cross-package technical review -> reviewer [review]
- wp-delivery: Delivery bundle preparation -> devops [delivery]

## Constraints
- Prefer explicit architecture over implicit behavior.
- Keep agent ownership boundaries clear.
- Do not auto-release without approval.
- Preserve artifacts and logs for audit.
- traceability
- repeatability
- approval gating
- controlled tool access
- artifact evidence
