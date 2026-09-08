import type { StreamEnvelope, GraphDelta, MessageSummary } from "@contract";
type MessageOrder = Pick<
  MessageSummary,
  | "stream_published_at"
  | "stream_item_id"
  | "stream_offset"
  | "member_index"
  | "standard_message_id"
>;
/** Entry into the stream, not source publication or collection, determines recency. */
export function compareMessageOrder(a: MessageOrder, b: MessageOrder) {
  const time =
    Date.parse(b.stream_published_at) - Date.parse(a.stream_published_at);
  if (time) return time;
  return (
      b.stream_offset - a.stream_offset ||
      b.member_index - a.member_index ||
      b.standard_message_id.localeCompare(a.standard_message_id)
    );
}
/** Align to a server-provided boundary. Reappearance schedules the NEXT boundary, never a catch-up read. */
export function nextMinuteDelay(
  nextMinuteAt: string,
  serverAsOf: string,
  receivedAt: number,
  now = Date.now(),
) {
  const boundary =
    Date.parse(nextMinuteAt) + receivedAt - Date.parse(serverAsOf);
  if (!Number.isFinite(boundary))
    throw new Error("Invalid server minute clock");
  const next =
    boundary > now
      ? boundary
      : boundary + (Math.floor((now - boundary) / 60_000) + 1) * 60_000;
  return next - now;
}
export function scheduleVisibleMinutes(
  clock: { nextMinuteAt: string; asOf: string; receivedAt: number },
  read: () => Promise<void>,
  visibility: Document = document,
) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let reading = false;
  const schedule = () => {
    clearTimeout(timer);
    if (stopped || visibility.hidden || reading) return;
    timer = setTimeout(
      async () => {
        if (stopped || visibility.hidden) return;
        reading = true;
        try {
          await read();
        } finally {
          reading = false;
          schedule();
        }
      },
      nextMinuteDelay(clock.nextMinuteAt, clock.asOf, clock.receivedAt),
    );
  };
  visibility.addEventListener("visibilitychange", schedule);
  schedule();
  return () => {
    stopped = true;
    clearTimeout(timer);
    visibility.removeEventListener("visibilitychange", schedule);
  };
}
export interface SseFrame {
  id: string;
  event: string;
  data: string;
}
/** Incremental parser: handles chunked UTF-8 via caller TextDecoder, CRLF and multi-line data. */
export class SseParser {
  private buffer = "";
  push(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const result: SseFrame[] = [];
    let match: RegExpExecArray | null;
    while ((match = /\r?\n\r?\n/.exec(this.buffer))) {
      const text = this.buffer.slice(0, match.index);
      if (new TextEncoder().encode(text).byteLength > 32_768)
        throw new Error("SSE frame exceeds budget");
      this.buffer = this.buffer.slice(match.index + match[0].length);
      const frame: SseFrame = { id: "", event: "message", data: "" };
      const data: string[] = [];
      for (const line of text.split(/\r?\n/)) {
        if (line.startsWith(":")) continue;
        const index = line.indexOf(":");
        const field = index < 0 ? line : line.slice(0, index);
        const value = index < 0 ? "" : line.slice(index + 1).replace(/^ /, "");
        if (field === "data") data.push(value);
        if (field === "event") frame.event = value;
        if (field === "id" && !value.includes("\0")) frame.id = value;
      }
      if (data.length) result.push({ ...frame, data: data.join("\n") });
    }
    if (new TextEncoder().encode(this.buffer).byteLength > 32_768)
      throw new Error("SSE frame exceeds budget");
    return result;
  }
}
/** Store one instance per auth/ticker/view/filter stream; baseline replacement creates a new instance. */
export class StreamCursor {
  cursor: string;
  private sequence = -1n;
  private ids = new Set<string>();
  constructor(
    readonly scope: string,
    readonly view: string,
    baseline: string,
  ) {
    this.cursor = baseline;
  }
  accept<T>(
    frame: SseFrame,
    envelope: StreamEnvelope<T>,
    apply: (payload: T) => void,
  ) {
    if (
      frame.id !== envelope.stream_cursor ||
      envelope.scope_key !== this.scope ||
      envelope.view_id !== this.view ||
      !/^\d+$/.test(envelope.sequence)
    )
      throw new Error("Stream scope mismatch; replace affected baseline");
    const sequence = BigInt(envelope.sequence);
    if (this.ids.has(envelope.event_id) || sequence <= this.sequence)
      return false;
    apply(envelope.payload); // Failed revision validation must NOT advance the resume cursor.
    this.sequence = sequence;
    this.cursor = frame.id;
    this.ids.add(envelope.event_id);
    if (this.ids.size > 256) this.ids.delete(this.ids.values().next().value!);
    return true;
  }
  async acceptAsync<T>(
    frame: SseFrame,
    envelope: StreamEnvelope<T>,
    apply: (payload: T) => Promise<void>,
  ) {
    if (
      frame.id !== envelope.stream_cursor ||
      envelope.scope_key !== this.scope ||
      envelope.view_id !== this.view ||
      !/^\d+$/.test(envelope.sequence)
    )
      throw new Error("Stream scope mismatch; replace affected baseline");
    if (
      this.ids.has(envelope.event_id) ||
      BigInt(envelope.sequence) <= this.sequence
    )
      return false;
    await apply(envelope.payload);
    return this.accept(frame, envelope, () => {});
  }
}
export function validateGraphRevision(current: number, delta: GraphDelta) {
  if (
    delta.previous_graph_revision !== current ||
    delta.graph_revision <= current
  )
    throw new Error("Graph revision gap; replace graph baseline");
}
