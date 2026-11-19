---
name: memory-deletion
description: Delete explicitly selected memory records with expected versions.
---

# نسيان المعلومات

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Require exact authorized record IDs and revisions; do not expand a deletion to source files or conversations. Invalidate dependent context and reject stale writes. Report backup retention limits; audit IDs without copying forgotten content.

Executor: `project-memory` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
