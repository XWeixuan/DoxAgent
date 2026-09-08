import type { ContentChunk, ContentRef } from "@contract";
import type { ApiClient } from "./api";
import { ApiFailure, queryString } from "./api";
/** Validates immutable identity and contiguous chunks; fetching remains controlled by the reader. */
export function appendContent(
  chunks: ContentChunk[],
  chunk: ContentChunk,
  expected: ContentRef,
): ContentChunk[] {
  if (
    chunk.content.content_id !== expected.content_id ||
    chunk.content.sha256 !== expected.sha256 ||
    chunk.content.content_type !== expected.content_type ||
    chunk.chunk_index !== chunks.length ||
    (chunks.length > 0 && chunks.at(-1)!.complete) ||
    chunk.complete !== (chunk.next_cursor === null)
  )
    throw new ApiFailure(
      "CONTENT_MISMATCH",
      "正文版本或分块顺序不一致，请重新打开该内容。",
    );
  return [...chunks, chunk];
}
export async function readContentChunk(
  api: ApiClient,
  ticker: string,
  content: ContentRef,
  cursor?: string,
  signal?: AbortSignal,
) {
  return api.request(
    "Content",
    `/tickers/${encodeURIComponent(ticker)}/contents/${encodeURIComponent(content.content_id)}` +
      queryString({ cursor }),
    { signal },
  );
}
export async function saveArtifact(
  api: ApiClient,
  path: string,
  signal?: AbortSignal,
) {
  const { blob, filename } = await api.download(path, signal);
  const url = URL.createObjectURL(blob),
    link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
