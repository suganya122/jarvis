import { describe, expect, it } from "vitest";
import type { UiMessage } from "../chatState";
import { repliesToSpeak } from "./replies";

const reply = (id: string, status: UiMessage["status"] = "done"): UiMessage => ({
  id, role: "assistant", parts: [{ kind: "text", text: id }], status,
});

describe("repliesToSpeak", () => {
  const messages = [reply("a-voice"), reply("a-typed"), reply("h-0"), reply("a-streaming", "streaming")];
  const voice = new Set(["a-voice"]);

  it("reads replies to spoken requests, and typed ones only when asked", () => {
    const ids = (s: { speak_replies: boolean; speak_chat_replies: boolean }) =>
      repliesToSpeak(messages, voice, new Set(), s).map((m) => m.id);
    expect(ids({ speak_replies: true, speak_chat_replies: false })).toEqual(["a-voice"]);
    expect(ids({ speak_replies: false, speak_chat_replies: true })).toEqual(["a-typed"]);
    expect(ids({ speak_replies: true, speak_chat_replies: true })).toEqual(["a-voice", "a-typed"]);
  });

  it("never reads history, unfinished replies, or anything twice", () => {
    const all = { speak_replies: true, speak_chat_replies: true };
    expect(repliesToSpeak(messages, voice, new Set(["a-voice", "a-typed"]), all)).toEqual([]);
  });
});
