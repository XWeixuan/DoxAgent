You are maintaining a Rolling Package Clustering Registry.

Assign the new Parent Occurrences to MCPs according to their Minimal Common Parent Event.

A Minimal Common Parent Event is the most specific common real-world event that has an independent occurrence boundary. Different facts, metrics, guidance, statements, or other aspects belong together when they are parts of that same event; separate real-world events remain separate.

Use an existing MCP when appropriate, create a new MCP when needed, and merge existing MCPs when the new evidence shows they represent the same Minimal Common Parent Event.

Every new occurrence_id must be assigned exactly once.

`canonical`: Short semantic name of the real-world Minimal Common Parent Event.

Use only new occurrence IDs provided in this request, and assign each one exactly once. Existing assignments and merges may reference only active mcp_id values provided in the registry. Each merge source may appear only once, and a merge source must not also be a keep_mcp_id.

Return only the Registry changes as JSON according to the required output schema.
