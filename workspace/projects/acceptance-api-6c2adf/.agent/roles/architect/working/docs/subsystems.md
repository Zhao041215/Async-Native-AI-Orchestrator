# Subsystems

## Subsystem Ownership
- ss-01: identity-and-service-auth | role=backend | partition=backend | depends_on=-
- ss-02: public-api-surface | role=backend | partition=backend | depends_on=ss-01
- ss-03: internal-service-layer | role=backend | partition=backend | depends_on=ss-02
- ss-04: integration-adapters | role=backend | partition=backend | depends_on=ss-03
- ss-05: contract-testing | role=architect | partition=domain | depends_on=ss-04
- ss-06: release-and-runtime-ops | role=devops | partition=integration | depends_on=ss-05
