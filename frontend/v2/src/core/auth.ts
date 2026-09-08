import { createClient, type Session } from "@supabase/supabase-js";
import { ApiClient, type AuthDriver } from "./api";
export type Identity = {
  id: string;
  email: string;
  sessionKey?: string;
} | null;
function identityOf(session: Session | null): Identity {
  if (!session) return null;
  let sessionKey: string | undefined;
  // JWT parsing only partitions a local cache. /auth/me remains the authorization authority.
  try {
    const encoded = session.access_token
      .split(".")[1]
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    const payload = JSON.parse(atob(encoded));
    if (typeof payload.session_id === "string") sessionKey = payload.session_id;
  } catch {
    /* An invalid token is rejected by the API, never promoted to permissions. */
  }
  return { id: session.user.id, email: session.user.email ?? "", sessionKey };
}
export interface AuthSession extends AuthDriver {
  identity: Identity;
  subscribe(listener: () => void): () => void;
  login(email: string, password: string): Promise<void>;
  logout(): Promise<void>;
}
export async function createAuth(): Promise<AuthSession> {
  let token: string | null = null;
  let identity: Identity = null;
  const listeners = new Set<() => void>();
  const publish = () => listeners.forEach((fn) => fn());
  const clearIdentity = () => {
    token = null;
    identity = null;
    publish();
  };
  const driver: AuthSession = {
    get identity() {
      return identity;
    },
    token: () => token,
    subscribe: (fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    invalidate: () => {
      clearIdentity();
      void client.auth.signOut({ scope: "local" }).catch(() => {});
    },
    refresh: async () => {
      const { data, error } = await client.auth.refreshSession();
      token = data.session?.access_token ?? null;
      return !error && !!token;
    },
    login: async (email, password) => {
      const { error } = await client.auth.signInWithPassword({
        email,
        password,
      });
      if (error) throw error;
    },
    logout: async () => {
      clearIdentity();
      await client.auth.signOut({ scope: "local" });
    },
  };
  const config = (
    await new ApiClient(driver).request("AuthConfig", "/auth/config", {
      public: true,
    })
  ).data;
  const client = createClient(
    config.supabase_url,
    config.supabase_publishable_key,
    {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: false,
        storageKey: "doxagent-v2-auth",
      },
    },
  );
  // Callback is synchronous: never call another Supabase auth method under its lock.
  client.auth.onAuthStateChange((_event, session) => {
    token = session?.access_token ?? null;
    const next = identityOf(session);
    if (
      identity?.id !== next?.id ||
      identity?.sessionKey !== next?.sessionKey
    ) {
      identity = next;
      publish();
    }
  });
  const { data, error } = await client.auth.getSession();
  if (error) throw error;
  token = data.session?.access_token ?? null;
  identity = identityOf(data.session);
  return driver;
}
