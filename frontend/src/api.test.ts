import { describe, expect, it } from "vitest";
import { takeTokenFromHash } from "./api";
import { socketUrl } from "./useChatSocket";

describe("takeTokenFromHash", () => {
  it("reads the token from the fragment", () => {
    expect(takeTokenFromHash("#token=abcdefghijklmnop_-12")).toBe("abcdefghijklmnop_-12");
    expect(takeTokenFromHash("#x=1&token=abcdefghijklmnopqr")).toBe("abcdefghijklmnopqr");
  });

  it("rejects missing or short tokens", () => {
    expect(takeTokenFromHash("")).toBeNull();
    expect(takeTokenFromHash("#token=short")).toBeNull();
    expect(takeTokenFromHash("#token=<script>alert(1)</script>")).toBeNull();
  });
});

describe("socketUrl", () => {
  it("uses the page host", () => {
    expect(socketUrl({ protocol: "http:", host: "127.0.0.1:8765" } as Location)).toBe("ws://127.0.0.1:8765/ws");
  });
});
