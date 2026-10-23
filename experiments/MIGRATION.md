# Repository organization

This migration reorganizes existing experiments; it does not create a new experiment or change recorded results.

| Old path | New path |
| --- | --- |
| `EXP-000-report.md` | `experiments/EXP-000/report.md` |
| `EXP-001-report.md` | `experiments/EXP-001/report.md` |
| `EXP-002-report.md` | `experiments/EXP-002/report.md` |
| `experiments/EXP-003.md` | `experiments/EXP-003/report.md` |
| `experiments/EXP-004.md` | `experiments/EXP-004/report.md` |
| `metrics.json` | `experiments/EXP-000/evidence/metrics.json` |
| `experiments/evidence/EXP-003/` | `experiments/EXP-003/evidence/` |
| `experiments/evidence/EXP-004/` | `experiments/EXP-004/evidence/` |
| `experiments/artifacts/EXP-XXX/` | `artifacts/EXP-XXX/` |
| Transport `forward-*.log` files previously beside evidence | `artifacts/EXP-XXX/transport-logs/`, preserving their relative run directories |

The three tracked legacy reports and metrics snapshot were moved with `git mv`. Later reports/evidence were already untracked and were moved without overwriting destinations. There was no EXP-002B report, so none was created. Historical report statuses, experiment IDs, measurements, timestamps, and raw captures remain intact; report paths and reproduction commands were updated for the new layout.

The existing ONNX model, metadata, Dockerfile, Maven project, application/test code, fixtures, and Kubernetes manifests remain in place. The accepted CSV remains at `data/synthetic_transactions.csv` because the unchanged metadata references it. Fresh training outputs now default to ignored `artifacts/EXP-000/`; explicit `--output-dir` still works. No training or model regeneration was performed.

The helpers use `artifacts/EXP-003/kubeconfig`, whose bytes and permissions were preserved. They continue to pass that kubeconfig and `kind-fraudops-exp003` explicitly. The default Kubernetes context was not changed. Docker build contexts exclude `artifacts/` and experiment documentation/evidence. Git ignores root runtime artifacts, kubeconfig files, `.kube` directories, and transport logs; reviewed application logs supporting existing results remain evidence.

## Validation

| Check | Result |
| --- | --- |
| Reports | Five existing reports relocated; original results/prose preserved except documented path/reproduction updates; report modification timestamps preserved |
| Evidence/runtime preservation | All 163 original non-report files match their pre-migration SHA-256, size, modification timestamp and permissions; 91 curated evidence files and 72 ignored runtime files remain |
| Functional files | All 55 inventoried application/test, model/metadata, dataset, Docker/Maven, and Kubernetes files unchanged from task start |
| Internal Markdown links | All 83 local links across seven documents resolve |
| Obsolete executable/documentation paths | None in live scripts, README, reports or ignore rules; historical captures and this path-mapping table intentionally retain old strings |
| Kustomize | All four roots render: `k8s/`, `k8s/base/`, `k8s/observability/`, `k8s/observability/api/` |
| Helper syntax | Bash syntax and Python in-memory compilation pass; training logic AST unchanged outside the CLI output-directory default/help |
| Java tests | Fresh Docker build-stage `mvn clean verify`: 16 tests, zero failures/errors/skips |
| Git/Docker exclusions | Runtime artifacts, kubeconfigs and transport logs ignored; curated reports/evidence remain eligible for Git; runtime/evidence excluded from Docker build context |
| Credential check | No private-key or kubeconfig certificate/key material found in curated experiment files; credentials and image archives remain ignored |
| Existing cluster | Read-only checks with migrated kubeconfig succeed; the same eight pods remain ready with unchanged identities/restart counts |
| Whitespace | `git diff --check` and staged-diff whitespace checks pass |

The Java test run used `docker buildx build --no-cache-filter build --target build --load -t fraudops-repository-organization-tests:local .`. Build logs, test summaries, renderings and validation results are retained under ignored `artifacts/repository-organization/`. No cluster creation, rollout, load trial, retraining, or full experiment verification was run.

The full file mapping, pre-migration SHA-256 hashes, file sizes, modification timestamps and permissions are retained locally in ignored `artifacts/repository-organization/inventory-before.json`. Raw captures preserve historical path strings verbatim; those are evidence of earlier commands, not executable references. No existing evidence was overwritten or deleted, and no runtime Kubernetes experiment was rerun.
