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
requests outside them fail closed. All writes default to preview. Apply calls
require the exact, unexpired preview digest and retain a durable SQLite receipt.
A Notion page is sent to FlowState only through the activation endpoint and
keeps its page and data-source provenance.

If a write is dispatched but its response is lost, the bridge retains the
operation as applying instead of issuing an immediate duplicate. After the
recovery grace period, the same operation ID may recover through exact Notion
read-back or the canonical FlowState replay contract even if the local preview
has since expired. Failures before any write is dispatched release the claim.
