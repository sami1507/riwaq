---
name: cost-budgeting
description: Reserve and settle shared operation limits before dispatch.
---

# إدارة تكلفة التشغيل

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Use integer native units and atomic shared reservations. Keep estimated, actual and unknown consumption distinct. No fabricated prices or currency conversion; paid retries count. Never raise the user ceiling automatically.

Executor: `skill-lifecycle` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
