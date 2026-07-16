# Notion FlowState Bridge

This directory is a standalone Hermes plugin. Install it by copying the whole
directory to `$HERMES_HOME/plugins/notion-flowstate-bridge`, then add
`notion-flowstate-bridge` to `plugins.enabled` in `config.yaml`.

Store the Notion credential in `NOTION_TOKEN`. An optional FlowState bearer
credential may be supplied as `FLOWSTATE_TOKEN`. Non-secret settings belong in
the plugin entry in `config.yaml`:

```yaml
plugins:
  enabled:
    - notion-flowstate-bridge
  entries:
    notion-flowstate-bridge:
      config:
        notion_data_source_id: "data-source-id"
        notion_idempotency_property: "Hermes operation ID"
        notion_writable_properties:
          - "Task title property"
          - "Status property"
          - "Due date property"
        flowstate_base_url: "http://127.0.0.1:8765"
        preview_ttl_seconds: 900
```

The configured idempotency property must exist in Notion and have type
`rich_text`. The data source and writable property names are exact allowlists;
requests outside them fail closed. The mutation tool exposes four explicit
actions: `create_task`, `update_properties`, `set_status`, and `archive_task`.
Archiving uses Notion's reversible `in_trash` state; the bridge never permanently
deletes a page.

Writable values are limited to property types whose API read-back has a stable
identity: title/rich text, number, select/multi-select, status, date, people,
checkbox, URL/email/phone, and relation. File properties and computed properties
fail during preview instead of producing an approval that cannot be verified.
Status changes must name an option present in the bound data-source schema.

All writes default to preview. A preview binds the exact data source, touched
property schema and types, normalized changes, target page, current
`last_edited_time`, request identity, and expiry. Apply re-reads the schema and
page before dispatch, fails closed on drift or a version conflict, then verifies
the result through a fresh page read. Notion does not expose a conditional page
update header, so there remains a narrow race between the final version read and
PATCH; post-write read-back and typed 409 handling prevent an unverified success
but cannot make that remote interval atomic.

Approved previews are stored in the profile-local SQLite file because exact
payload recovery is required after a lost response. Tokens are never persisted.
Verified receipts contain hashes and version evidence rather than copied task
content. A Notion page is sent to FlowState only through the separate activation
endpoint and keeps its page and data-source provenance.

If a write is dispatched but its response is lost, the bridge retains the
operation as applying instead of issuing an immediate duplicate. After the
recovery grace period, the same operation ID may recover through exact Notion
read-back or the canonical FlowState replay contract even if the local preview
has since expired. Failures before any write is dispatched release the claim.
