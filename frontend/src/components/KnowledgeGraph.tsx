import { CENTER, HEIGHT, MAX_NODES, WIDTH, edges, layout, shortName } from "../knowledgeLayout";
import type { KnowledgeEntity } from "../contractsHttp";

interface Props {
  entities: KnowledgeEntity[];
  selected: string | null;
  onSelect: (id: string) => void;
}

/** You in the middle, the people and places you've told it about around you, and how they connect. */
export function KnowledgeGraph({ entities, selected, onSelect }: Props) {
  const nodes = layout(entities);
  const at = new Map(nodes.map((n) => [n.entity.id, n]));
  const lines = edges(nodes);
  const spot = (id: string) => (id === "you" ? CENTER : at.get(id));
  return (
    <figure className="kg-figure">
      <svg
        className="kg-graph"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="group"
        aria-label={`Knowledge graph: you and ${nodes.length} people and places`}
      >
        {nodes.map(({ entity, x, y }) =>
          lines.some((l) => l.from === entity.id || l.to === entity.id) ? null : (
            <line key={`loose-${entity.id}`} className="kg-edge kg-edge-loose" x1={CENTER.x} y1={CENTER.y} x2={x} y2={y} />
          ),
        )}
        {lines.map((l) => {
          const a = spot(l.from);
          const b = spot(l.to);
          if (!a || !b) return null;
          return (
            <g key={`${l.from}-${l.to}-${l.label}`}>
              <line className={l.toYou ? "kg-edge kg-edge-you" : "kg-edge"} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
              <text className="kg-edge-label" x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 3} textAnchor="middle">
                {l.label}
              </text>
            </g>
          );
        })}
        <g className="kg-node kg-node-you">
          <circle cx={CENTER.x} cy={CENTER.y} r={30} />
          <text x={CENTER.x} y={CENTER.y + 4} textAnchor="middle">
            You
          </text>
        </g>
        {nodes.map(({ entity, x, y }) => (
          <g
            key={entity.id}
            className={`kg-node kg-node-${entity.type}${selected === entity.id ? " kg-selected" : ""}`}
            role="button"
            tabIndex={0}
            aria-label={`${entity.name}, ${entity.type}${entity.relation ? `, your ${entity.relation}` : ""}`}
            aria-pressed={selected === entity.id}
            onClick={() => onSelect(entity.id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(entity.id);
              }
            }}
          >
            <circle cx={x} cy={y} r={22} />
            <text x={x} y={y + 4} textAnchor="middle">
              {shortName(entity.name)}
            </text>
          </g>
        ))}
      </svg>
      {entities.length > MAX_NODES && (
        <figcaption className="hint">Showing the {MAX_NODES} you mention most; everyone is in the list below.</figcaption>
      )}
    </figure>
  );
}
