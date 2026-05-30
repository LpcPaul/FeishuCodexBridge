# Changelog

## 0.3.0 - 2026-05-30

- Enabled CardKit 2.0 by default for Codex-generated JSON 2.0 cards.
- Added CardKit 2.0 guidance for form cards, including `form` containers and `multi_select_static` multi-select fields.
- Added automatic conversion from legacy `checkbox_group` test cards to CardKit 2.0 form cards.
- Added submit-button callback stamping for CardKit form submissions.
- Documented `checkbox_group` card send failures and the required CardKit permission.

## 0.2.0 - 2026-05-30

- Added Codex-declared Feishu card blocks and button callback routing back into the same Codex session.
- Added optional CardKit 2.0 send-by-card-id support.
- Added optional Feishu Docx document creation from Codex-declared document blocks and long replies.
- Added `/docs` status command and documentation for the extra card/doc permissions.
- Added initial Feishu permission JSON covering message, card, document, and group creation scopes.
- Added post-install Feishu callback checklist to prevent card button `code: 200340` setup issues.
- Fixed launchd startup by using the `certifi` CA bundle for Feishu WebSocket TLS verification.

## 0.1.0 - 2026-05-29

Initial public release.

- Added Feishu WebSocket event bridge to local Codex CLI.
- Added private-chat topic management with a 2-hour idle boundary.
- Added Feishu topic-boundary card with "continue previous topic" and "keep current topic" actions.
- Added long-running task tracking and periodic progress messages.
- Added mobile-readable Codex reply context for Feishu/chat clients.
- Added macOS launchd installer, uninstall script, and runtime configuration template.
- Added docs for Feishu app setup, permissions, installation, architecture, and troubleshooting.
