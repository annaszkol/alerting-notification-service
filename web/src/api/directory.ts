// Lists many screens need for pickers (people, teams, runbooks, rules). Any signed-in user may read them.
import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { RuleDoc, Runbook, Source, Team, User } from "./types";

export const useUsers = () => useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users"), staleTime: 60_000 });
export const useTeams = () => useQuery({ queryKey: ["teams"], queryFn: () => api<Team[]>("/teams"), staleTime: 60_000 });
export const useRunbooks = () => useQuery({ queryKey: ["runbooks"], queryFn: () => api<Runbook[]>("/runbooks") });
export const useRules = () => useQuery({ queryKey: ["rules"], queryFn: () => api<RuleDoc[]>("/rules") });

/** Sources are admin-only in the API; other screens fall back to a free-text source id. */
export const useSources = (enabled: boolean) =>
  useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("/sources"), enabled, staleTime: 60_000 });
