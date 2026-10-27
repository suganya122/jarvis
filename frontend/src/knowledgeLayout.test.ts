import { describe, expect, it } from "vitest";
import { CENTER, MAX_NODES, edges, layout, shortName } from "./knowledgeLayout";
import type { KnowledgeEntity } from "./contractsHttp";

const person = (id: string, over: Partial<KnowledgeEntity> = {}): KnowledgeEntity => ({
  id,
  name: id,
  type: "person",
  aliases: [],
  relation: "",
  mentions: 1,
  last_seen_at: 0,
  facts: [],
  ...over,
});

describe("knowledge graph layout", () => {
  it("puts the most mentioned people on the inner ring around you", () => {
    const nodes = layout([person("a", { mentions: 1 }), person("b", { mentions: 9 }), person("c", { mentions: 5 })]);
    expect(nodes.map((n) => n.entity.id)).toEqual(["b", "c", "a"]);
    for (const n of nodes) expect(Math.hypot((n.x - CENTER.x) / 1.35, n.y - CENTER.y)).toBeCloseTo(130, 0);
  });

  it("shows at most the 24 you mention most", () => {
    const many = Array.from({ length: 40 }, (_, i) => person(`p${i}`, { mentions: i }));
    expect(layout(many)).toHaveLength(MAX_NODES);
  });

  it("joins people to you by how they're related, and to each other by what links them", () => {
    const nodes = layout([
      person("srinith", {
        relation: "sibling",
        facts: [
          { id: "1", text: "lives in Pune", predicate: "lives_in", object_id: "pune", since: 0, quote: "", current: true },
          { id: "2", text: "lives in Chennai", predicate: "lives_in", object_id: "chennai", since: 0, quote: "", current: false },
        ],
      }),
      person("pune", { type: "place" }),
    ]);
    expect(edges(nodes)).toEqual([
      { from: "srinith", to: "you", label: "sibling", toYou: true },
      { from: "srinith", to: "pune", label: "lives in", toYou: false },
    ]); // the older, replaced fact draws nothing
  });

  it("keeps long names in their bubble", () => {
    expect(shortName("Srinivasan Ramachandran")).toBe("Srinivasan R…");
    expect(shortName("Priya")).toBe("Priya");
  });
});
