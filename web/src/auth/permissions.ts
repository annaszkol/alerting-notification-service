// Who may do what in the UI. Mirrors the checks in services/api (auth.Principal, routers/*): the API is the
// authority, the UI only hides or disables what would end in 403, so people are not offered dead buttons.
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Me, Owner } from "../api/types";

export interface Permissions {
  me: Me | undefined;
  userId: string | undefined;
  isAdmin: boolean;
  /** Teams I belong to (any role). Admin counts as a member everywhere, like `Principal.is_member`. */
  isMember(team: string): boolean;
  /** Manager of the team, or admin (`Principal.is_manager`). */
  isManager(team: string): boolean;
  /** Manager of at least one team, or admin: sees the manager screens (runbook import, suggestions to decide). */
  managesAnything: boolean;
  /** Rules: manager of the owner team, the owner user, or admin (`Principal.can_edit_owned`). */
  canEditOwner(owner: Owner | null | undefined): boolean;
  /** Teams I may own rules / runbooks for: the teams I manage (admin: all of `allTeams`). */
  managedTeams(allTeams?: { id: string }[]): string[];
}

export function permissionsFor(me: Me | undefined): Permissions {
  const isAdmin = Boolean(me?.isAdmin);
  const roles = new Map(me?.teams.map((t) => [t.id, t.role]) ?? []);
  const isMember = (team: string) => isAdmin || roles.has(team);
  const isManager = (team: string) => isAdmin || roles.get(team) === "manager";
  return {
    me,
    userId: me?.user.id,
    isAdmin,
    isMember,
    isManager,
    managesAnything: isAdmin || [...roles.values()].includes("manager"),
    canEditOwner(owner) {
      if (!me || !owner) return false;
      if (isAdmin) return true;
      if ("team" in owner) return roles.get(owner.team) === "manager";
      return owner.user === me.user.id;
    },
    managedTeams(allTeams) {
      if (isAdmin) return (allTeams ?? me?.teams ?? []).map((t) => t.id);
      return me?.teams.filter((t) => t.role === "manager").map((t) => t.id) ?? [];
    },
  };
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me"), staleTime: 30_000 });
}

export function usePermissions(): Permissions & { loading: boolean } {
  const me = useMe();
  return { ...permissionsFor(me.data), loading: me.isLoading };
}

export function ownerLabel(owner: Owner | null | undefined): string {
  if (!owner) return "—";
  return "team" in owner ? `team ${owner.team}` : `personal (${owner.user})`;
}
