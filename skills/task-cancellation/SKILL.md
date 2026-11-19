---
name: task-cancellation
description: Request cancellation and observe actual worker acknowledgment.
---

# إلغاء المهام

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Set cancellation state, propagate stop to supported workers, and block late publication immediately. Distinguish request from acknowledgment and disclose effects already committed. Never automatically restart a cancelled task.

Executor: `skill-lifecycle` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
