# Message Bus operations

Use these MCP tools (underscore names map one-to-one to canonical dotted names):

- `monitoring_list_sources`, `monitoring_get_source`: inspect global SourceDefinition, adapter_ref,
  parameter schema/defaults, scheduler group/constraints, enabled state, version and revisions.
- `monitoring_get_default_profile`, `monitoring_update_default_profile`: inspect/change the template
  for future first starts. The initial default contains only Finnhub and Benzinga.
- `monitoring_get_ticker_config`: read ticker state, complete bindings and poll states.
- `monitoring_update_ticker_config`: create/update one binding. Always send the complete intended
  `source_parameters` object after a read-merge; then send polling, streaming, enabled and reason.
- `monitoring_list_status`, `monitoring_recent_events`, `monitoring_list_failures`: health and repair
  evidence. Message Bus `source_poll_failure` resolves automatically after poll recovery; never edit
  its database or fabricate resolution.
- `monitoring_register_source`, `monitoring_update_source`: global source creation and revision.
  If parameter schema changes, update all affected bindings atomically using `binding_patches`.
- `monitoring_hard_delete_source`: destructive control-plane deletion. Use only for an explicitly
  obsolete/invalid source with a concrete reason; immutable message and audit history is retained.

Configuration precedence for a binding is: its explicit source parameters/polling/streaming, else
the Default Profile values materialized on first start, else SourceDefinition defaults. Later source
or profile changes do not silently rewrite an existing binding. Source registration and ticker use are
separate decisions. There is no source_id allow/deny policy: a source enters Runtime only when that
ticker has an enabled binding.

Disabling a binding stops future polling only. Already published stream items continue into Runtime.
Never implement dual poll/write with v1.
