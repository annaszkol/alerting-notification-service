import { ME_ADMIN, ME_MANAGER, ME_MEMBER } from "../test-utils";
import { permissionsFor } from "./permissions";

describe("permissions (mirror of services/api auth.Principal)", () => {
  it("member: own personal rules only, member of own team", () => {
    const p = permissionsFor(ME_MEMBER);
    expect(p.canEditOwner({ team: "rates" })).toBe(false);
    expect(p.canEditOwner({ user: "dave" })).toBe(true);
    expect(p.canEditOwner({ user: "anna" })).toBe(false);
    expect(p.isMember("rates")).toBe(true);
    expect(p.isMember("pricing")).toBe(false);
    expect(p.isManager("rates")).toBe(false);
    expect(p.managesAnything).toBe(false);
    expect(p.managedTeams()).toEqual([]);
  });

  it("manager: rules of the managed team, not of other teams", () => {
    const p = permissionsFor(ME_MANAGER);
    expect(p.canEditOwner({ team: "rates" })).toBe(true);
    expect(p.canEditOwner({ team: "pricing" })).toBe(false);
    expect(p.isManager("rates")).toBe(true);
    expect(p.managesAnything).toBe(true);
    expect(p.managedTeams()).toEqual(["rates"]);
  });

  it("admin: everything, every team", () => {
    const p = permissionsFor(ME_ADMIN);
    expect(p.canEditOwner({ team: "pricing" })).toBe(true);
    expect(p.canEditOwner({ user: "anna" })).toBe(true);
    expect(p.isMember("pricing")).toBe(true);
    expect(p.managedTeams([{ id: "a" }, { id: "b" }])).toEqual(["a", "b"]);
  });

  it("nobody signed in: nothing", () => {
    const p = permissionsFor(undefined);
    expect(p.canEditOwner({ user: "x" })).toBe(false);
    expect(p.isAdmin).toBe(false);
  });
});
