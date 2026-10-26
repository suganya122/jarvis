import { useCallback, useEffect, useRef, useState } from "react";
import { WS_SUBPROTOCOL, WS_TOKEN_PREFIX, type ClientMessage, type ServerMessage } from "./contracts";

export type ConnectionState = "connecting" | "open" | "closed";

const MAX_BACKOFF_MS = 10_000;

export function socketUrl(loc: Location = window.location): string {
  const scheme = loc.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${loc.host}/ws`;
}

/** WebSocket with auth via subprotocol and automatic reconnect with backoff. */
export function useChatSocket(token: string, onMessage: (msg: ServerMessage) => void) {
  const [state, setState] = useState<ConnectionState>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const handler = useRef(onMessage);

  useEffect(() => {
    handler.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    let stopped = false;
    let attempt = 0;
    let timer: number | undefined;

    const connect = () => {
      setState("connecting");
      const ws = new WebSocket(socketUrl(), [WS_SUBPROTOCOL, WS_TOKEN_PREFIX + token]);
      wsRef.current = ws;
      ws.onopen = () => {
        attempt = 0;
        setState("open");
      };
      ws.onmessage = (event) => {
        try {
          handler.current(JSON.parse(String(event.data)) as ServerMessage);
        } catch {
          // ignore malformed frames
        }
      };
      ws.onclose = () => {
        wsRef.current = null;
        if (stopped) return;
        setState("closed");
        const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** attempt);
        attempt += 1;
        timer = window.setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      wsRef.current?.close(1000);
    };
  }, [token]);

  const send = useCallback((msg: ClientMessage): boolean => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(msg));
    return true;
  }, []);

  return { state, send };
}
