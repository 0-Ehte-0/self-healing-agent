export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    cache: "no-store",
    credentials: "same-origin",
    signal:
      options.signal ||
      AbortSignal.timeout(options.method === "POST" ? 20000 : 12000),
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new ApiError(
      response.status,
      typeof data.detail === "string"
        ? data.detail
        : data.message || `Request failed (${response.status})`,
    );
  return data;
}
export type Row = Record<string, any>;
export const terminal = new Set([
  "RESOLVED",
  "ESCALATED",
  "ROLLEDBACK",
  "FAILED",
]);
export const stages = [
  "DETECTED",
  "TRIAGED",
  "DIAGNOSED",
  "PLANNED",
  "EXECUTING",
  "VERIFYING",
  "RESOLVED",
];
export const nice = (value?: string) =>
  (value || "Unknown").replaceAll("_", " ").toLowerCase();
export const stamp = (value?: string) =>
  value
    ? new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "—";
export const duration = (seconds?: number | null) =>
  seconds == null
    ? "—"
    : seconds < 60
      ? `${Math.round(seconds)}s`
      : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
