// Shared test helpers: providers and a fetch stub that answers by "METHOD /path".
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import type { Me } from "./api/types";

export function renderWith(ui: ReactNode, path: string | { pathname: string; state?: unknown } = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          {ui}
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

type Handler = unknown | ((body: unknown) => unknown);

/** Stub `fetch`: routes maps "GET /rules" (path without /api and query) to a JSON body or a function of the body. */
export function stubApi(routes: Record<string, Handler>) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = url.replace(/^\/api/, "").split("?")[0];
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path, body });
    const key = `${method} ${path}`;
    if (!(key in routes)) return new Response(JSON.stringify({ error: { code: "not_found", message: key } }), { status: 404 });
    const handler = routes[key];
    const result = typeof handler === "function" ? (handler as (b: unknown) => unknown)(body) : handler;
    return new Response(JSON.stringify(result ?? null), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export const ME_MEMBER: Me = { user: { id: "dave", name: "Dave", timezone: "UTC", isAdmin: false }, isAdmin: false, teams: [{ id: "rates", name: "Rates", role: "member" }] };
export const ME_MANAGER: Me = { user: { id: "mia", name: "Mia", timezone: "UTC", isAdmin: false }, isAdmin: false, teams: [{ id: "rates", name: "Rates", role: "manager" }] };
export const ME_ADMIN: Me = { user: { id: "root", name: "Root", timezone: "UTC", isAdmin: true }, isAdmin: true, teams: [] };
