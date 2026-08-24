import type { QueryInput, StreamEvent, StreamEventName } from "./types";

const EVENT_NAMES = new Set<StreamEventName>([
  "accepted",
  "planning",
  "retrieving",
  "sources",
  "delta",
  "heartbeat",
  "done",
  "error",
]);
const MAX_SSE_FRAME_CHARS = 2_000_000;

export async function streamAnswer(
  input: QueryInput,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `${import.meta.env.VITE_API_BASE ?? "/api/v1"}/queries/stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
      signal,
    },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const message = payload?.error?.message ?? `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  if (!response.body) {
    throw new Error("浏览器没有收到流式响应体");
  }
  await parseSseStream(response.body, onEvent);
}

export async function parseSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let previousSequence = -1;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replaceAll("\r\n", "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = decodeFrame(frame);
      if (event) {
        requireIncreasingSequence(event, previousSequence);
        previousSequence = event.sequence;
        onEvent(event);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (buffer.length > MAX_SSE_FRAME_CHARS) {
      throw new Error("SSE 事件超过客户端大小限制");
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = decodeFrame(buffer);
    if (event) {
      requireIncreasingSequence(event, previousSequence);
      onEvent(event);
    }
  }
}

function decodeFrame(frame: string): StreamEvent | null {
  const data = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  const decoded: unknown = JSON.parse(data);
  if (!isStreamEvent(decoded)) {
    throw new Error("服务端返回了无效的 SSE 事件");
  }
  return decoded;
}

function isStreamEvent(value: unknown): value is StreamEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Partial<StreamEvent>;
  return (
    typeof event.event === "string" &&
    EVENT_NAMES.has(event.event as StreamEventName) &&
    typeof event.request_id === "string" &&
    Number.isInteger(event.sequence) &&
    (event.sequence ?? -1) >= 0 &&
    !!event.data &&
    typeof event.data === "object"
  );
}

function requireIncreasingSequence(event: StreamEvent, previous: number): void {
  if (event.sequence <= previous) {
    throw new Error("服务端 SSE sequence 未严格递增");
  }
}
