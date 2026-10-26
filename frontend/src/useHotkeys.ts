import { useEffect, useRef, type RefObject } from "react";
import { stopSpeaking } from "./voice/speech";
import type { useVoice } from "./voice/useVoice";

/** Ctrl+Shift+K stops everything. With the typing box on, hold Ctrl+Space to talk in this tab
 * (otherwise the assistant listens for its wake word by itself). */
export function useHotkeys(
  voice: RefObject<ReturnType<typeof useVoice> | null>,
  onStopAll: () => void,
  talkKey: boolean,
) {
  const stopAll = useRef(onStopAll);
  useEffect(() => {
    stopAll.current = onStopAll;
  });

  useEffect(() => {
    let holding = false;
    const down = (e: KeyboardEvent) => {
      if (talkKey && e.code === "Space" && e.ctrlKey && !e.repeat && !holding) {
        e.preventDefault();
        holding = true;
        void voice.current?.start("chat", performance.now());
      } else if (e.key === "Escape") {
        stopSpeaking();
      }
    };
    const up = (e: KeyboardEvent) => {
      if (holding && (e.code === "Space" || e.key === "Control")) {
        holding = false;
        voice.current?.stop();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [voice, talkKey]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        stopAll.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
