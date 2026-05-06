# Subsystems

## Subsystem Ownership
- ss-01: identity-and-access | role=backend | partition=backend | depends_on=-
- ss-02: product-web-app | role=frontend | partition=frontend | depends_on=ss-01
- ss-03: application-api | role=backend | partition=backend | depends_on=ss-02
- ss-04: domain-services | role=backend | partition=backend | depends_on=ss-03
- ss-05: data-and-reporting | role=devops | partition=integration | depends_on=ss-04
- ss-06: test-and-release-pipeline | role=devops | partition=integration | depends_on=ss-05
