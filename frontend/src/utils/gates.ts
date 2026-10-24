export interface GateLike {
  ok?: boolean
  ready?: boolean
  reason?: string
}

/** 有 ready 时以 ready 为准，否则以 ok 为准。没有 gate 时不阻断。 */
export function gateBlocked(gate?: GateLike | null): boolean {
  if (!gate) return false
  if (gate.ready !== undefined) return gate.ready !== true
  return gate.ok !== true
}
