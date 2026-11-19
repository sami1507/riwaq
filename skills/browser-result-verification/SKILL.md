---
name: browser-result-verification
description: Verify a browser action through current operation readback.
---

# التحقق من نتيجة الإجراء

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Check the intended account/resource/version through the trusted server. A toast is not proof. Unknown effects require inspection before replay. This capability verifies Riwaq task downloads and saved operations, not arbitrary third-party browser actions.

Executor: `skill-readback` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
