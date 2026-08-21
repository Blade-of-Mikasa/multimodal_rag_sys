import { describe, expect, it } from "vitest";

import { parseSseStream } from "./api";
import type { StreamEvent } from "./types";

describe("parseSseStream", () => {
  it("parses frames split across arbitrary UTF-8 chunks", async () => {
    const encoded = new TextEncoder().encode(
      'event: delta\ndata: {"event":"delta","request_id":"r1",' +
        '"sequence":1,"data":{"text":"你好"}}\n\n' +
        'event: done\ndata: {"event":"done","request_id":"r1",' +
        '"sequence":2,"data":{"finish_reason":"completed"}}\n\n',
    );
    const split = encoded.indexOf(0xe4) + 1;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded.slice(0, split));
        controller.enqueue(encoded.slice(split));
        controller.close();
      },
    });
    const events: StreamEvent[] = [];

    await parseSseStream(body, (event) => events.push(event));

    expect(events.map((event) => event.event)).toEqual(["delta", "done"]);
    expect(events[0].data.text).toBe("你好");
  });

  it("rejects an event outside the transport contract", async () => {
    const body = new Response(
      'data: {"event":"unknown","request_id":"r1","sequence":0,"data":{}}\n\n',
    ).body!;
    await expect(parseSseStream(body, () => undefined)).rejects.toThrow(
      "无效的 SSE 事件",
    );
  });

  it("rejects replayed or out-of-order sequence numbers", async () => {
    const body = new Response(
      'data: {"event":"delta","request_id":"r1","sequence":1,"data":{}}\n\n' +
        'data: {"event":"done","request_id":"r1","sequence":1,"data":{}}\n\n',
    ).body!;
    await expect(parseSseStream(body, () => undefined)).rejects.toThrow(
      "未严格递增",
    );
  });
});
