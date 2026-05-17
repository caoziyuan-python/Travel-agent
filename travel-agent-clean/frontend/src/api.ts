import type { ChatResponse } from './types';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export async function sendMessage(
  message: string,
  sessionId?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Server error ${res.status}: ${text}`);
  }

  return res.json() as Promise<ChatResponse>;
}

export async function resetSession(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/chat/${sessionId}`, { method: 'DELETE' });
}
