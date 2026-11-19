---
name: text-to-speech
description: Generate and persist audio using a discovered compatible local voice.
---

# تحويل النص إلى كلام

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Require a discovered enabled voice and matching explicit language. Preserve the exact spoken text, validate WAV frames and duration, and deliver through the authorized artifact route. Stop the worker on cancellation. No cloud fallback or invented language support.

Executor: `local-speech` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
