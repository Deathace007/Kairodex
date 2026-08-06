/**
 * Thin fetch wrapper against the FastAPI backend (kairodex/api). One env
 * var covers both server components (Next.js runs on the same VM as the
 * API — see kairodex.api.main's own docstring on why it binds
 * 127.0.0.1) and the browser (reached through the same SSH tunnel port
 * number, e.g. `ssh -L 8000:localhost:8000 ...`) — see docs/PROGRESS.md
 * §12's "how to reach the dashboards" note.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiGet<T>(path: string, revalidateSecs = 10): Promise<T> {
  const res = await fetch(`${API_BASE}/api${path}`, { next: { revalidate: revalidateSecs } });
  if (!res.ok) {
    throw new ApiError(res.status, `GET ${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, `POST ${path} -> ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/** Same shape everywhere a fetch might fail — used to render "data
 * unavailable" instead of crashing a whole dashboard over one panel. */
export async function apiGetSafe<T>(path: string, revalidateSecs = 10): Promise<T | null> {
  try {
    return await apiGet<T>(path, revalidateSecs);
  } catch {
    return null;
  }
}
