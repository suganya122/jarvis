interface StateProps {
  loading: boolean;
  error: string | null;
  empty: boolean;
  emptyText: string;
  onRetry: () => void;
  what: string;
}

/** The loading, error and empty versions every list needs. Renders nothing when there's data. */
export function LoadState({ loading, error, empty, emptyText, onRetry, what }: StateProps) {
  if (error) {
    return (
      <div className="state state-error" role="alert">
        <p>
          Couldn't load {what}: {error}
        </p>
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  if (loading && empty) {
    return (
      <p className="state" role="status">
        Loading {what}…
      </p>
    );
  }
  if (empty) return <p className="state state-empty">{emptyText}</p>;
  return null;
}
