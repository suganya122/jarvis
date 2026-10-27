import type { KnowledgeEntity } from "./contractsHttp";

export interface GraphNode {
  entity: KnowledgeEntity;
  x: number;
  y: number;
}

export interface GraphEdge {
  from: string;
  to: string;
  label: string;
  /** Joins the user ("you") rather than two others. */
  toYou: boolean;
}

export const WIDTH = 640;
export const HEIGHT = 440;
export const CENTER = { x: WIDTH / 2, y: HEIGHT / 2 };
export const MAX_NODES = 24;
const INNER = 8;

/** People around "you": up to eight on an inner ring, the rest on an outer one. Most mentioned first. */
export function layout(entities: KnowledgeEntity[]): GraphNode[] {
  const shown = [...entities].sort((a, b) => b.mentions - a.mentions).slice(0, MAX_NODES);
  const rings: KnowledgeEntity[][] = [shown.slice(0, INNER), shown.slice(INNER)];
  const radii = [Math.min(HEIGHT * 0.3, 130), Math.min(HEIGHT * 0.43, 190)];
  return rings.flatMap((ring, r) =>
    ring.map((entity, i) => {
      const angle = (2 * Math.PI * i) / ring.length - Math.PI / 2 + (r === 1 ? Math.PI / ring.length : 0);
      return { entity, x: CENTER.x + radii[r] * Math.cos(angle) * 1.35, y: CENTER.y + radii[r] * Math.sin(angle) };
    }),
  );
}

/** Lines: each person to "you" (labelled with how they're related), and between people who are linked. */
export function edges(nodes: GraphNode[]): GraphEdge[] {
  const ids = new Set(nodes.map((n) => n.entity.id));
  const out: GraphEdge[] = [];
  for (const { entity } of nodes) {
    if (entity.relation) out.push({ from: entity.id, to: "you", label: entity.relation, toYou: true });
    for (const fact of entity.facts) {
      if (fact.current && fact.object_id && ids.has(fact.object_id) && fact.object_id !== entity.id) {
        out.push({ from: entity.id, to: fact.object_id, label: fact.predicate.replaceAll("_", " "), toYou: false });
      }
    }
  }
  return out;
}

/** Names that fit in a bubble. */
export function shortName(name: string, max = 13): string {
  return name.length <= max ? name : `${name.slice(0, max - 1)}…`;
}
