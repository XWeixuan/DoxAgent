import { it, expect } from "vitest";
import { StreamCursor } from "../src/core/updates";
import { validateStream } from "../src/core/api";
it("async stream apply commits cursor only after targeted fetch succeeds", async () => {
  const cursor = new StreamCursor("scope", "view", "baseline");
  const envelope = {
    event_id: "one",
    stream_cursor: "next",
    scope_key: "scope",
    view_id: "view",
    sequence: "1",
    emitted_at: "2026-09-08T00:00:00Z",
    payload: {},
  };
  const frame = { id: "next", event: "message.delta", data: "" };
  await expect(
    cursor.acceptAsync(frame, envelope, async () => {
      throw new Error("fetch failed");
    }),
  ).rejects.toThrow();
  expect(cursor.cursor).toBe("baseline");
  let calls = 0;
  await cursor.acceptAsync(frame, envelope, async () => {
    calls++;
  });
  await cursor.acceptAsync(frame, envelope, async () => {
    calls++;
  });
  expect(cursor.cursor).toBe("next");
  expect(calls).toBe(1);
});
it("stream wire validation rejects malformed graph revisions before reducing", () => {
  expect(() =>
    validateStream("GraphStream", { payload: { graph_revision: -1 } }),
  ).toThrow();
});
