import { createGenerator } from "ts-json-schema-generator";
import { writeFileSync, readFileSync, renameSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
const schema = createGenerator({
  path: "src/core/schema-source.ts",
  tsconfig: "tsconfig.json",
  type: "*",
  expose: "export",
  skipTypeCheck: true,
}).createSchema("*");
// Scalar and resource invariants specified in API Contract §2 (not expressible by TS aliases).
Object.assign(schema.definitions.Count, {
  type: "integer",
  minimum: 0,
  maximum: Number.MAX_SAFE_INTEGER,
});
Object.assign(schema.definitions.Id, { minLength: 1 });
Object.assign(schema.definitions.DecimalString, {
  pattern: "^-?[0-9]+(\\.[0-9]+)?$",
});
Object.assign(schema.definitions.Instant, {
  pattern:
    "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$",
});
for (const [name, definition] of Object.entries(schema.definitions)) {
  if (name.startsWith("Resource<"))
    definition.allOf = [
      {
        if: {
          properties: { state: { enum: ["AVAILABLE", "EMPTY", "PARTIAL"] } },
        },
        then: { properties: { data: { not: { type: "null" } } } },
      },
      {
        if: {
          properties: {
            state: {
              enum: ["NOT_PRODUCED", "UNAVAILABLE", "FORBIDDEN", "ERROR"],
            },
          },
        },
        then: { properties: { data: { type: "null" } } },
      },
    ];
}
Object.assign(schema.definitions.InitializationProgress.properties.steps, {
  minItems: 6,
  maxItems: 6,
});
const contract = readFileSync(
  "../../dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts",
);
writeFileSync(
  "src/core/wire-schema.json.tmp",
  JSON.stringify({
    ...schema,
    $comment: `Source SHA256 ${createHash("sha256").update(contract).digest("hex")}`,
  }) + "\n",
);
renameSync("src/core/wire-schema.json.tmp", "src/core/wire-schema.json");
console.log(
  "Generated V2 validators from shared wire types (no V1 dependency).",
);

// Python keeps server-only compatibility aliases; update cost roots and missing dependencies.
const backendPath = "../../src/doxagent/api_v2/wire_schema.json";
if (existsSync(backendPath)) {
  const backend = JSON.parse(readFileSync(backendPath, "utf8"));
  const definitions = JSON.parse(
    JSON.stringify(schema.definitions).replaceAll("#/definitions/", "#/$defs/"),
  );
  const add = (name, replace = false) => {
    if (!replace && backend.$defs[name]) return;
    backend.$defs[name] = definitions[name];
    const walk = (value) => {
      if (!value || typeof value !== "object") return;
      if (value.$ref?.startsWith("#/$defs/"))
        add(decodeURIComponent(value.$ref.slice(8)));
      for (const child of Object.values(value)) walk(child);
    };
    walk(definitions[name]);
  };
  for (const name of [
    "CostReport",
    "CostReportTotals",
    "CostReportRow",
    "CostSummary",
    "BusStatus",
    "SourceStatus",
    "PollTimeliness",
    "PollCadence",
    "BusMetrics",
    "MessageSummary",
    "ExecutionSummary",
  ])
    add(name, true);
  backend.source_sha256 = createHash("sha256").update(contract).digest("hex");
  writeFileSync(backendPath + ".tmp", JSON.stringify(backend, null, 2) + "\n");
  renameSync(backendPath + ".tmp", backendPath);
}
