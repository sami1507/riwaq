---
name: file-search
description: Find authorized files by name or indexed content in the current project.
---

# البحث في ملفات المستخدم

Activate only for the matching operation or system lifecycle boundary. Do not substitute this workflow for an unrelated request.

Inputs: trusted session/project plus the existing SkillRequest and referenced resource versions.

Search only the current trusted project. Return stable file IDs and distinguish filename from content matches. Do not treat snippets as complete reading or silently choose between identical names before mutation.

Executor: `project-extracted-content` in Riwaq. Return the server operation state, output references, evidence and unresolved limitations. Missing dependencies mean blocked or partial; never simulated success. Verify the real output and recheck scope before delivery.

Acceptance and exact tested limits: `docs/skills/SKILLS_BATCH_02_ACCEPTANCE.md` in the project repository. This text does not authorize extra tools or network destinations.
