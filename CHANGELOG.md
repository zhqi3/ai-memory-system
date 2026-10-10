# Changelog

## 0.2.0-rc.2 — prepared locally, 2026-10-10

- Remove mandatory communication style from the public navigation protocol and
  memory templates. Each user can choose language, tone, length and style.
- Remove the fixed Chinese language and prose character limit from optional
  diagnosis instructions; keep structured output, scope and resource guards.
- Identify the revised protocol as `ai-navigation/1.0.0-rc.2`; existing private
  adoptions and the prior release candidate are preserved.

## 0.2.0-rc.1 — prepared locally, 2026-10-10

- Add opt-in MOSS adoption, pinned protocol/body loading, project/global switches,
  bounded handoff export and a portable skill.
- Preserve legacy compact startup for workspaces without MOSS; installed projects
  and future registrations inherit the explicitly adopted default.
- Refuse partial context on protocol conflicts or recognized interrupted memory
  transactions; retain checks when navigation is disabled.
- Include pending cross-agent capture support for Hermes, Claude Code and actual
  ChatGPT response bundles, maintenance filtering, metadata health and opt-in
  bounded exception diagnosis.
- Add synthetic navigation regressions, a standalone lifecycle demo and release
  preparation instructions. Real project records and host settings are excluded.

This candidate is not a published GitHub release. Prior local/client acceptance
is not transferred into a claim that this portable package supports every host.
