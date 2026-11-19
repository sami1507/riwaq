# Visual project/session authority — GAP-038

The previous visual storage and job paths used `knowledge.require_project`
as an existence check. A valid local session could continue to read or write a
visual project after its guarded ownership changed. Session middleware alone
did not establish project authority. This is an additional discovered P0 gap,
not part of the original inventory of 36 gaps.

`visual_authority.require_project` now derives `guard.Actor` from the trusted
local session and rechecks it through `guard.check_actor`. Asset, design,
version, list, export, update, deletion and job paths all use this check. A
worker receives an actor captured by the server at enqueue time; request/model
fields cannot choose an owner or role. Worker authorization is checked before
dispatch, during the supervised process and before publishing assets/results.
Revocation stops the process and prevents publication. Failure records contain
bounded generic messages rather than exception text or protected content.

Bundle streaming uses a fresh database authority check between chunks, so an
older read transaction does not preserve permission after revocation. Asset
responses use `private, no-store`. Historical assets/designs remain protected
by the same checks; the change is not limited to new QR assets.

## Before and after

Evidence is in `reports/closure-20261116/visual-authority/`:

- `before.xml` and `api-before.json`: two failing regressions. Ten read routes
  plus six write/job operations remained reachable after ownership withdrawal;
  a revoked server-side session context also retained access.
- `final-fixed-fixture.xml`: 49 passing targeted checks. The intermediate
  `final.xml` contains a test-fixture error (missing required job fields), retained
  for traceability; its fixture was corrected without weakening expectations.
- `api-after.json`: revoked resources are unavailable across all tested
  routes. Tests also check revocation after image processing, between bundle
  chunks, before/after worker execution and in the actual process supervisor.
- `../visual-normalize/live-final/report.json`: real authenticated upload and
  Docker execution, withdrawal while the container runs, verified cleanup and
  no added asset. Fixture job bodies in unit tests are explicitly simulated.

`tests/test_visual_authority.py` exercises the real API/session guard with
synthetic content. Older visual storage tests now create trusted test sessions;
their decoder stub is explicitly a unit simulation. The live Docker tests use
no decoder mock. This evidence covers the visual subsystem; it does not certify
authorization in unrelated services.
