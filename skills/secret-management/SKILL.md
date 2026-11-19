---
name: secret-management
description: Inspect credential availability without exposing values.
---

# إدارة الأسرار

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Read only configured status from the OS store. Never include a value in model context, logs or outputs. Missing credentials remain unavailable; cloud generation remains gated. Setup and revocation use the existing settings workflow.

Executor: `os-credential-store` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
