# Work Packages

## Package List
- wp-plan: Repository and requirement decomposition | owner=planner | phase=planning | partition=planning | subsystems=ss-01, ss-02, ss-03, ss-04, ss-05, ss-06
- wp-arch: Cross-subsystem architecture baseline | owner=architect | phase=architecture | partition=architecture | subsystems=ss-01, ss-02, ss-03, ss-04, ss-05, ss-06
- wp-ss-01: Implement identity-and-service-auth | owner=backend | phase=implementation | partition=backend | subsystems=ss-01
- wp-ss-02: Implement public-api-surface | owner=backend | phase=implementation | partition=backend | subsystems=ss-02
- wp-ss-03: Implement internal-service-layer | owner=backend | phase=implementation | partition=backend | subsystems=ss-03
- wp-ss-04: Implement integration-adapters | owner=backend | phase=implementation | partition=backend | subsystems=ss-04
- wp-ss-05: Implement contract-testing | owner=architect | phase=architecture | partition=domain | subsystems=ss-05
- wp-ss-06: Implement release-and-runtime-ops | owner=devops | phase=approval_required | partition=integration | subsystems=ss-06
- wp-verify: Cross-package verification | owner=qa | phase=verification | partition=verification | subsystems=ss-01, ss-02, ss-03, ss-04, ss-05, ss-06
- wp-review: Cross-package technical review | owner=reviewer | phase=review | partition=review | subsystems=ss-01, ss-02, ss-03, ss-04, ss-05, ss-06
- wp-delivery: Delivery bundle preparation | owner=devops | phase=approval_required | partition=delivery | subsystems=ss-01, ss-02, ss-03, ss-04, ss-05, ss-06
