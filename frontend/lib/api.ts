export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
  info: unknown;
  status: number;

  constructor(message: string, status: number, info: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.info = info;
  }
}

export const fetcher = async <T = unknown>(url: string): Promise<T> => {
  const res = await fetch(`${API_BASE}${url}`);
  if (!res.ok) {
    const info = await res.json().catch(() => null);
    throw new ApiError("An error occurred while fetching the data.", res.status, info);
  }
  return res.json();
};

export const postData = async <T = unknown>(url: string, data?: unknown): Promise<T> => {
  const res = await fetch(`${API_BASE}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${url} failed`);
  return res.json();
};

export const deleteData = async <T = unknown>(url: string): Promise<T> => {
  const res = await fetch(`${API_BASE}${url}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`DELETE ${url} failed`);
  return res.json();
};
