import type { AuthSession } from "../../src/core/auth";
export function createTestAuth(): AuthSession {
  const identity = { id: "synthetic-reviewer", email: "reviewer@demo.local" };
  return {
    identity,
    token: () => "synthetic-demo-token",
    subscribe: () => () => {},
    refresh: async () => true,
    invalidate: () => {},
    login: async () => {},
    logout: async () => {},
  };
}
