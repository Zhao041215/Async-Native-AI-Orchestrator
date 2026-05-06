# Subsystems

## Subsystem Ownership
- ss-01: tenant-and-identity | role=backend | partition=backend | depends_on=-
- ss-02: web-dashboard | role=frontend | partition=frontend | depends_on=ss-01
- ss-03: billing-and-subscriptions | role=backend | partition=backend | depends_on=ss-02
- ss-04: application-api | role=backend | partition=backend | depends_on=ss-03
- ss-05: audit-and-observability | role=devops | partition=integration | depends_on=ss-04
- ss-06: test-and-release-pipeline | role=devops | partition=integration | depends_on=ss-05
