import { api, ApiError, getToken, LOGOUT_EVENT, queryString, setToken } from "./client";

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue(new Response(body === null ? null : JSON.stringify(body), { status }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  setToken(null);
});

it("sends the bearer token and JSON body", async () => {
  setToken("t0k3n");
  const fetchMock = respond(200, { ok: true });
  vi.stubGlobal("fetch", fetchMock);
  await api("/auth/login", { json: { userId: "anna" } });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/auth/login");
  expect(init.method).toBe("POST");
  expect(init.headers.Authorization).toBe("Bearer t0k3n");
  expect(JSON.parse(init.body)).toEqual({ userId: "anna" });
});

it("turns the error format into ApiError", async () => {
  vi.stubGlobal("fetch", respond(409, { error: { code: "conflict", message: "alert 7 is resolved" } }));
  const error = (await api("/alerts/7/ack", { method: "POST" }).catch((e: unknown) => e)) as ApiError;
  expect(error).toBeInstanceOf(ApiError);
  expect([error.status, error.code, error.message]).toEqual([409, "conflict", "alert 7 is resolved"]);
});

it("401 clears the token and signals logout", async () => {
  setToken("expired");
  const onLogout = vi.fn();
  window.addEventListener(LOGOUT_EVENT, onLogout);
  vi.stubGlobal("fetch", respond(401, { error: { code: "unauthorized", message: "token expired" } }));
  await expect(api("/me")).rejects.toBeInstanceOf(ApiError);
  expect(getToken()).toBeNull();
  expect(onLogout).toHaveBeenCalledOnce();
  window.removeEventListener(LOGOUT_EVENT, onLogout);
});

it("builds query strings with repeated parameters", () => {
  expect(queryString({ team: "rates", severity: ["critical", "error"], q: "", cursor: null })).toBe(
    "?team=rates&severity=critical&severity=error",
  );
  expect(queryString({})).toBe("");
});
