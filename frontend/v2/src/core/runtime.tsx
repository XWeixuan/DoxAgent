import {
  createContext,
  useContext,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import type { AuthSession } from "./auth";
import { ApiClient } from "./api";
import { createQueryClient } from "./cache";
export function createRuntime(auth: AuthSession) {
  const query = createQueryClient();
  const api = new ApiClient(auth);
  let generation = 0;
  const identityKey = () =>
    `${auth.identity?.id ?? "anonymous"}:${auth.identity?.sessionKey ?? ""}`;
  let currentId = identityKey();
  const dispose = auth.subscribe(() => {
    if (currentId !== identityKey()) {
      currentId = identityKey();
      generation++;
      api.reset();
      void query.cancelQueries();
      query.clear();
    }
  });
  return {
    auth,
    api,
    query,
    get scope() {
      return `${generation}:${auth.identity?.id ?? "anonymous"}`;
    },
    dispose,
  };
}
export type Runtime = ReturnType<typeof createRuntime>;
const RuntimeContext = createContext<Runtime | null>(null);
export const RuntimeProvider = ({
  runtime,
  children,
}: {
  runtime: Runtime;
  children: ReactNode;
}) => (
  <RuntimeContext.Provider value={runtime}>
    <QueryClientProvider client={runtime.query}>{children}</QueryClientProvider>
  </RuntimeContext.Provider>
);
export function useRuntime() {
  const runtime = useContext(RuntimeContext);
  if (!runtime) throw new Error("Missing Runtime");
  return runtime;
}
export function useIdentity() {
  const { auth } = useRuntime();
  return useSyncExternalStore(auth.subscribe, () => auth.identity);
}
