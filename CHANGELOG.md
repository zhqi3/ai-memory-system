# Changelog

## Unreleased

- Correct the README and release documentation to reflect the published rc.2
  prerelease. These documentation changes do not replace the published tag or assets.
- Limit the supported and validated environment to the tested macOS host with
  Python 3.12.14. Other Python versions, other macOS environments and Linux remain
  unvalidated; Windows is unsupported by the current POSIX implementation.

## 0.2.0-rc.2 — published prerelease, 2026-10-10

[GitHub release](https://github.com/zhqi3/ai-memory-system/releases/tag/v0.2.0-rc.2).
This is a public prerelease, not a stable release.

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

The rc.1 candidate was prepared locally and was not published as a GitHub release.
Prior local/client acceptance does not establish support on every host.
