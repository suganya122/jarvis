import { describe, expect, it } from "vitest";
import { chatReducer, initialChatState, messageText, pendingApprovals, type ChatState } from "./chatState";
import type { ServerMessage } from "./contracts";

const server = (state: ChatState, message: ServerMessage) => chatReducer(state, { kind: "server", message });

describe("chatReducer", () => {
  it("streams deltas into the assistant message and finishes", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "hi", tier: "cheap" });
    expect(s.pendingRequestId).toBe("r1");
    s = server(s, { type: "chat.started", request_id: "r1", trace_id: "t", session_id: "s1", tier: "cheap" });
    s = server(s, { type: "chat.thinking", request_id: "r1" });
    expect(s.messages[1].status).toBe("thinking");
    s = server(s, { type: "chat.delta", request_id: "r1", text: "Hel" });
    s = server(s, { type: "chat.delta", request_id: "r1", text: "lo" });
    s = server(s, {
      type: "chat.done",
      request_id: "r1",
      model: "m",
      stop_reason: "end_turn",
      usage: { input_tokens: 1, output_tokens: 2, cache_read_tokens: 0, cache_write_tokens: 0 },
      cost_usd: 0.001,
      latency_ms: 10,
      first_token_ms: 5,
      route: "model",
      timings: {},
    });
    expect(s.sessionId).toBe("s1");
    expect(s.pendingRequestId).toBeNull();
    expect(s.messages.map(messageText)).toEqual(["hi", "Hello"]);
    expect(s.messages[1].status).toBe("done");
    expect(s.messages[1].meta?.cost_usd).toBe(0.001);
  });

  it("puts a voice turn's target on the user's message", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "read this", tier: "cheap" });
    s = server(s, { type: "chat.started", request_id: "r1", trace_id: "t", session_id: "s1", tier: "cheap",
                    target: "Screen 2 · Chrome · Inbox" });
    expect(s.messages[0].target).toBe("Screen 2 · Chrome · Inbox");
  });

  it("marks cancelled and errored turns", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "hi", tier: "cheap" });
    s = server(s, { type: "chat.cancelled", request_id: "r1" });
    expect(s.messages[1].status).toBe("cancelled");
    expect(s.pendingRequestId).toBeNull();

    s = chatReducer(s, { kind: "local_send", requestId: "r2", text: "again", tier: "cheap" });
    s = server(s, { type: "chat.error", request_id: "r2", code: "offline", message: "Offline" });
    expect(s.messages[3].status).toBe("error");
    expect(s.lastError).toBe("Offline");
    expect(s.pendingRequestId).toBeNull();
  });

  it("loads a stored session", () => {
    const s = chatReducer(initialChatState, {
      kind: "load_session",
      sessionId: "s9",
      messages: [{ role: "user", text: "q", trace_id: "t", created_at: 0 }],
    });
    expect(s.sessionId).toBe("s9");
    expect(s.messages[0]).toMatchObject({ role: "user", status: "done" });
    expect(messageText(s.messages[0])).toBe("q");
  });

  it("tracks tool calls, approvals and their outcome in order", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "save", tier: "cheap" });
    s = server(s, { type: "chat.delta", request_id: "r1", text: "Saving." });
    s = server(s, {
      type: "tool.started",
      request_id: "r1",
      call_id: "c1",
      tool: "write_text_file",
      summary: "Create ~/a.txt",
      risk: "write",
    });
    s = server(s, {
      type: "approval.request",
      approval_id: "ap1",
      request_id: "r1",
      call_id: "c1",
      tool: "write_text_file",
      risk: "write",
      summary: "Create ~/a.txt",
      reason: "fs.write is set to ask",
      args: { path: "~/a.txt" },
      untrusted_context: false,
      can_remember: true,
      expires_in_s: 120,
    });
    expect(pendingApprovals(s)).toHaveLength(1);
    expect(pendingApprovals(s)[0].status).toBe("waiting");

    s = chatReducer(s, { kind: "approval_sent", approvalId: "ap1", approved: true });
    expect(pendingApprovals(s)).toHaveLength(0);
    s = server(s, { type: "tool.finished", request_id: "r1", call_id: "c1", status: "ok", preview: "Wrote", duration_ms: 3 });
    s = server(s, { type: "chat.delta", request_id: "r1", text: "Done." });

    const parts = s.messages[1].parts;
    expect(parts.map((p) => p.kind)).toEqual(["text", "tool", "text"]);
    expect(parts[1]).toMatchObject({ status: "ok", preview: "Wrote" });
  });

  it("follows the pause state", () => {
    const s = server(initialChatState, { type: "control.state", paused: true });
    expect(s.paused).toBe(true);
    expect(chatReducer(s, { kind: "new_session" }).paused).toBe(true);
  });

  it("keeps the prompt and instant route for 'Ask Claude instead'", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "open firefox", tier: "cheap" });
    s = server(s, {
      type: "chat.done",
      request_id: "r1",
      model: "instant",
      stop_reason: "end_turn",
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
      cost_usd: 0,
      latency_ms: 12,
      first_token_ms: 11,
      route: "instant",
      timings: { route_ms: 0.2, action_ms: 10 },
      skill: null,
    });
    expect(s.messages[1].prompt).toBe("open firefox");
    expect(s.messages[1].meta?.route).toBe("instant");
  });

  it("puts highlight boxes on the screenshot they refer to", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "where?", tier: "cheap" });
    s = server(s, { type: "tool.started", request_id: "r1", call_id: "c1", tool: "capture_screen", summary: "Shot", risk: "read" });
    s = server(s, {
      type: "tool.finished", request_id: "r1", call_id: "c1", status: "ok", preview: "", duration_ms: 5,
      image: "data:image/jpeg;base64,AAAA", capture_id: "s1", capture_width: 1280, capture_height: 800,
    });
    s = server(s, { type: "tool.started", request_id: "r1", call_id: "c2", tool: "highlight_area", summary: "Point", risk: "read" });
    s = server(s, {
      type: "tool.finished", request_id: "r1", call_id: "c2", status: "ok", preview: "", duration_ms: 1,
      highlight: { capture_id: "s1", x: 10, y: 20, width: 30, height: 40, label: "Click here" },
    });
    const shot = s.messages[1].parts[0];
    expect(shot.kind === "tool" && shot.capture).toEqual({ id: "s1", width: 1280, height: 800 });
    expect(shot.kind === "tool" && shot.highlights?.[0].label).toBe("Click here");
  });

  it("keeps the skill note from a finished turn", () => {
    let s = chatReducer(initialChatState, { kind: "local_send", requestId: "r1", text: "fire up gimp", tier: "cheap" });
    s = server(s, {
      type: "chat.done", request_id: "r1", model: "skill", stop_reason: "end_turn",
      usage: { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0 },
      cost_usd: 0, latency_ms: 30, first_token_ms: 29, route: "skill", timings: {},
      skill: { event: "replayed", skill_id: "k", name: "fire up {app}", successes: 3, needed: 2 },
    });
    expect(s.messages[1].meta?.route).toBe("skill");
    expect(s.messages[1].meta?.skill?.event).toBe("replayed");
  });
});
