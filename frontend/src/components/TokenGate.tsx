import { useState } from "react";
import { takeTokenFromHash } from "../api";

export function TokenGate({ onSubmit }: { onSubmit: (token: string) => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <main className="gate">
      <h1>Connect to your local assistant</h1>
      <p>
        Open the link printed in the terminal when the daemon starts, or paste it (or just the token) below. The token
        stays in this browser tab only.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const raw = value.trim();
          const fromLink = raw.includes("#") ? takeTokenFromHash(raw.slice(raw.indexOf("#"))) : null;
          const token = fromLink ?? raw;
          if (!/^[A-Za-z0-9_-]{16,}$/.test(token)) {
            setError("That doesn't look like a valid token.");
            return;
          }
          onSubmit(token);
        }}
      >
        <label htmlFor="token-input">Link or token</label>
        <input
          id="token-input"
          type="password"
          autoComplete="off"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "token-error" : undefined}
        />
        {error && (
          <p id="token-error" className="error-line" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="primary">
          Connect
        </button>
      </form>
    </main>
  );
}
