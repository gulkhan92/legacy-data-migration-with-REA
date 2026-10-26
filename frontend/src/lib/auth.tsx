import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, sessionApi, type Me, type Role } from "./api";

type AuthState = {
  me: Me | null;
  /** True while it is not yet known whether someone is signed in (only on pages that need to know). */
  loading: boolean;
  /** A page that depends on the session (the sign in page, the console) calls this once to have it checked. */
  watch: () => void;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

/**
 * The session cookie cannot be read by scripts, so the browser would have to ask the server on every page whether anyone is signed in,
 * and the server answers 401 to every visitor who is not (a failed request in the console of every public page). Instead this flag,
 * set at sign in and cleared at sign out, says that a session may exist. It is only a hint for the user interface: the server is the
 * authority and the console always asks it.
 */
const HINT = "legacylift.signed-in";

function readHint(): boolean {
  try {
    return window.localStorage.getItem(HINT) === "1";
  } catch {
    return false;
  }
}

function writeHint(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(HINT, "1");
    else window.localStorage.removeItem(HINT);
  } catch {
    /* storage may be blocked: the hint is optional */
  }
}

/** The console always asks the server. The sign in page asks only when the hint says a session may exist, so a visitor makes no failing request. */
const onSessionRoute = (): boolean => typeof window !== "undefined" && /^\/app(\/|$)/.test(window.location.pathname);

export const hasSessionHint = readHint;

/** Where each role lands after signing in. The console itself is built in the next phase; these are its entry pages. */
export function homeFor(role: Role): string {
  return role === "analyst" ? "/app/migrations" : "/app/overview";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [watching, setWatching] = useState<boolean>(() => readHint() || onSessionRoute());
  const query = useQuery({
    queryKey: ["session"],
    enabled: watching,
    queryFn: async ({ signal }) => {
      try {
        return await sessionApi.me(signal);
      } catch (error) {
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          writeHint(false);
          return null; // not signed in: not an error
        }
        throw error;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });

  const login = useCallback(
    async (email: string, password: string) => {
      const me = await sessionApi.login(email, password);
      writeHint(true);
      setWatching(true);
      client.setQueryData(["session"], me);
      return me;
    },
    [client],
  );

  const logout = useCallback(async () => {
    try {
      await sessionApi.logout();
    } finally {
      writeHint(false);
      client.setQueryData(["session"], null);
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
    }
  }, [client]);

  const watch = useCallback(() => setWatching(true), []);
  const value = useMemo<AuthState>(
    () => ({ me: query.data ?? null, loading: watching && query.isPending, watch, login, logout }),
    [query.data, query.isPending, watching, watch, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Asks the server once whether someone is signed in. For pages whose content depends on it. */
export function useSessionCheck({ onlyWithHint = false } = {}): void {
  const { watch } = useAuth();
  useEffect(() => {
    if (!onlyWithHint || readHint()) watch();
  }, [watch, onlyWithHint]);
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
