export const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export const fetcher = async (url: string) => {
  const res = await fetch(`${API_BASE}${url}`);
  if (!res.ok) {
    const error = new Error('An error occurred while fetching the data.');
    (error as any).info = await res.json().catch(() => null);
    (error as any).status = res.status;
    throw error;
  }
  return res.json();
};

// Common api helper to POST
export const postData = async (url: string, data?: any) => {
  const res = await fetch(`${API_BASE}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: data ? JSON.stringify(data) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${url} failed`);
  return res.json();
};

export const deleteData = async (url: string) => {
  const res = await fetch(`${API_BASE}${url}`, {
    method: 'DELETE',
  });
  if (!res.ok) throw new Error(`DELETE ${url} failed`);
  return res.json();
};
