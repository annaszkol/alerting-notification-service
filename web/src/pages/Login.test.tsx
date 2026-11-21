import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { getToken, setToken } from "../api/client";
import { Login } from "./Login";

afterEach(() => {
  vi.unstubAllGlobals();
  setToken(null);
});

it("lists seeded users and signs in with the chosen one", async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === "/api/auth/users") {
      return new Response(JSON.stringify([{ id: "anna", name: "Anna Kowalska" }, { id: "admin", name: "Ada", isAdmin: true }]));
    }
    return new Response(JSON.stringify({ token: "jwt-anna", expiresAt: "2026-11-22T00:00:00Z", user: { id: "anna", name: "Anna" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  const onLogin = vi.fn();

  render(
    <MantineProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Login onLogin={onLogin} />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );

  expect(await screen.findByText("Anna Kowalska")).toBeInTheDocument();
  expect(screen.getByText("admin · admin")).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Sign in" })[0]);
  await waitFor(() => expect(onLogin).toHaveBeenCalledWith("jwt-anna"));
  expect(getToken()).toBe("jwt-anna");
});
