import { createGenerator } from "ts-json-schema-generator";
import { writeFileSync, readFileSync } from "node:fs";
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
  "src/core/wire-schema.json",
  JSON.stringify({
    ...schema,
    $comment: `Source SHA256 ${createHash("sha256").update(contract).digest("hex")}`,
  }) + "\n",
);
console.log(
  "Generated V2 validators from shared wire types (no V1 dependency).",
);
