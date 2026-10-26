import type { UiMessage } from "./chatState";
import type { ModelTier } from "./contracts";

const TIER_SIZE: Record<ModelTier, string> = { cheap: "small", balanced: "balanced", max: "largest" };

/** "Last ask: small model, no escalation. Skill replays and instant commands cost $0." */
export function lastAskLine(messages: UiMessage[], tiers: { tier: ModelTier; model: string }[]): string {
  const meta = [...messages].reverse().find((m) => m.role === "assistant" && m.meta)?.meta;
  const free = "Skill replays and instant commands cost $0.";
  if (!meta) return `No asks yet. ${free}`;
  if (meta.route !== "model") return `Last ask: ${meta.route === "skill" ? "skill replay" : "instant command"}, no AI used. ${free}`;
  const tier = tiers.find((t) => t.model === meta.model)?.tier;
  const size = tier ? TIER_SIZE[tier] : meta.model;
  const escalation = meta.escalated ? `moved up to Balanced (${meta.escalated})` : "no escalation";
  return `Last ask: ${size} model, ${escalation}. ${free}`;
}
