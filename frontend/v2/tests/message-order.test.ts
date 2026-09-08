import { describe, expect, it } from "vitest";
import { compareMessageOrder } from "../src/core/updates";

describe("message stream ordering", () => {
  it("places a later published buffer ahead of a more recently collected immediate message", () => {
    const buffered = {
      standard_message_id: "buffered",
      stream_item_id: "batch",
      stream_offset: 2,
      member_index: 0,
      collected_at: "2026-09-08T08:00:00Z",
      stream_published_at: "2026-09-08T10:00:00Z",
    };
    const immediate = {
      standard_message_id: "immediate",
      stream_item_id: "single",
      stream_offset: 1,
      member_index: 0,
      collected_at: "2026-09-08T09:00:00Z",
      stream_published_at: "2026-09-08T09:00:00Z",
    };
    expect(
      [immediate, buffered]
        .sort(compareMessageOrder)
        .map((r) => r.standard_message_id),
    ).toEqual(["buffered", "immediate"]);
  });
  it("keeps members of the same buffer in contract descending member order", () => {
    const rows = [0, 2, 1].map((member_index) => ({
      standard_message_id: String(member_index),
      stream_item_id: "batch",
      stream_offset: 3,
      member_index,
      stream_published_at: "2026-09-08T10:00:00Z",
    }));
    expect(rows.sort(compareMessageOrder).map((r) => r.member_index)).toEqual([
      2, 1, 0,
    ]);
  });
  it("orders distinct stream items at the same instant by native offset", () => {
    const rows = [3, 12, 8].map((stream_offset) => ({
      standard_message_id: String(stream_offset), stream_item_id: `s-${stream_offset}`,
      stream_offset, member_index: 0, stream_published_at: "2026-09-08T10:00:00Z",
    }));
    expect(rows.sort(compareMessageOrder).map(r => r.stream_offset)).toEqual([12, 8, 3]);
  });
});
