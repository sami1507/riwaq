---
name: freshness-check
description: Compare known source dates with the time scope of the question.
---

# فحص حداثة المعلومات

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Separate publication, update, fetch and event times. A recent fetch alone does not prove current facts. Use the registered-source research gateway for approved refresh; otherwise show the last dated evidence and unresolved freshness.

Executor: `registered-source-evidence` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
