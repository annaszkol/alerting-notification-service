import { teamInput, teamProblems, userProblems } from "./Admin";

describe("onboarding a team in one call (spec §9, §13 no. 20)", () => {
  const draft = {
    id: "fx",
    name: "FX",
    members: [{ userId: "anna", role: "member" as const }],
    newUsers: [{ id: "zoe", name: "Zoe", email: "", phone: "+48500100200", timezone: "Europe/Warsaw", isAdmin: false, role: "manager" as const }],
  };

  it("adds new people to members with their role and cleans empty contacts", () => {
    expect(teamInput(draft)).toEqual({
      id: "fx",
      name: "FX",
      members: [
        { userId: "anna", role: "member" },
        { userId: "zoe", role: "manager" },
      ],
      newUsers: [{ id: "zoe", name: "Zoe", email: null, phone: "+48500100200", timezone: "Europe/Warsaw", isAdmin: false }],
    });
  });

  it("blocks duplicate logins and bad phone numbers", () => {
    expect(teamProblems({ ...draft, newUsers: [{ ...draft.newUsers[0], id: "anna" }] }, ["anna"])).toContain("New person 1: login id anna already exists");
    expect(userProblems({ id: "x", name: "X", phone: "500100200", timezone: "UTC", isAdmin: false })).toEqual(["Phone in international format, e.g. +48500100200"]);
    expect(teamProblems(draft, ["anna"])).toEqual([]);
  });
});
