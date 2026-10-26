import { useEffect, useRef, type RefObject } from "react";
import { messageText, type UiMessage } from "../chatState";
import { speak } from "./speech";

export interface SpeakSettings {
  speak_replies: boolean;
  speak_chat_replies: boolean;
}

/** Replies to read aloud now: finished, from this tab (not loaded history), not read yet. */
export function repliesToSpeak(
  messages: UiMessage[],
  spokenRequests: ReadonlySet<string>,
  alreadySpoken: ReadonlySet<string>,
  settings: SpeakSettings,
): UiMessage[] {
  return messages.filter(
    (m) =>
      m.role === "assistant" &&
      m.status === "done" &&
      m.id.startsWith("a-") &&
      !alreadySpoken.has(m.id) &&
      (spokenRequests.has(m.id) ? settings.speak_replies : settings.speak_chat_replies),
  );
}

/** Reads replies aloud as they finish; changing the setting never reads out what's already on screen. */
export function useSpokenReplies(
  messages: UiMessage[],
  settings: SpeakSettings | null,
  spokenRequests: RefObject<Set<string>>,
): void {
  const spoken = useRef(new Set<string>());
  const latest = useRef(messages);
  useEffect(() => {
    latest.current = messages;
  });
  useEffect(() => {
    for (const m of latest.current) if (m.status === "done") spoken.current.add(m.id);
  }, [settings?.speak_replies, settings?.speak_chat_replies]);
  useEffect(() => {
    if (!settings) return;
    for (const m of repliesToSpeak(messages, spokenRequests.current, spoken.current, settings)) {
      spoken.current.add(m.id);
      speak(messageText(m));
    }
  }, [messages, settings, spokenRequests]);
}
