/** "3 min ago", "2 h ago", or a date for older times. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

export function formatTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}
