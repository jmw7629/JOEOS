# Workspace presentation profile

The optional `workspace.json` file beside the installed runtime customizes presentation without editing application source. With no file, the current PROJECT_BYTE name, Joe AI assistant label, Joe/Mike shortcuts and Orbital appearance are preserved. Nothing is written on startup or when this configuration is read.

For a different private installation, the administrator can create a UTF-8 JSON file with these fields:

```json
{
  "schema_version": 1,
  "display_name": "Acme Operations",
  "assistant_name": "Atlas",
  "owner_shortcuts": ["Avery", "Taylor"]
}
```

Refresh the page after changing the file. Workspace and assistant names may contain up to 48 characters; up to six people shortcuts are supported. Shortcuts filter existing records by their owner label. An empty shortcut list is supported. Values are rendered as text. Custom long labels fit the existing mobile layout, including selected-owner headings and scope captions. Agent-map labels remain inside their compact nodes; people shortcuts keep horizontal scrolling.

`GET /api/workspace-profile` exposes only this presentation profile. It is public within the installation's existing network boundary, like its application shell. Never put credentials, endpoints or execution policy in this file: unknown fields are rejected. Invalid, oversized, non-regular or symlinked files produce an explicit unavailable response and a warning in Settings, with the default appearance retained.

This profile does not rename authenticated users, change stable principal IDs, grant permissions, add team members, create projects, seed records, enable models/executors, or alter existing settings. It is a presentation foundation for separate private installations, not organization data isolation or complete reusable onboarding. Personal task ownership, authentication, private installation packaging and enterprise security remain governed separately under issue #53.

The installer does not create or overwrite this optional file. Keep it in the administrator's installation backup and configuration management process. The app's existing source checksums remain enforced.

Verification covers absent/default configuration, valid custom labels, invalid/oversized/symlink/FIFO inputs, no database writes, literal markup labels, mobile/desktop overflow, first-notification branding, delayed-profile completion during logout, and preservation of session, tasks and personal preferences. The required command-center CI job runs both Python profile tests and the actual browser profile flow.

A narrow responsive correction also keeps the optional Chat context controls within the actual phone viewport after resizing from desktop. It retains the existing two-column arrangement and does not change the owner design.
