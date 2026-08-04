const API_URL = process.env.NEXT_PUBLIC_FMS_API_URL || "https://api-test.phloz.app/fms";

export const UNAUTHORISED_EVENT = "fms-unauthorised";

export function logout() {
  if (typeof window !== "undefined") sessionStorage.removeItem("fms_token");
}

export async function fmsRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (!API_URL) throw new Error("NEXT_PUBLIC_FMS_API_URL is not configured");
  const token = typeof window !== "undefined" ? sessionStorage.getItem("fms_token") : null;
  const response = await fetch(`${API_URL}/api/v1/${path.replace(/^\//, "")}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Token ${token}` } : {}), ...options.headers },
  });
  // A revoked token, or a login that has been deactivated, should return the operator to
  // the sign-in screen rather than leaving a workspace that can no longer load anything.
  if (response.status === 401 && token) {
    logout();
    window.dispatchEvent(new Event(UNAUTHORISED_EVENT));
    throw new Error("Your session has ended. Please sign in again.");
  }
  if (!response.ok) throw new Error((await response.text()) || `API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

export async function login(username: string, password: string) {
  const result = await fmsRequest<{ token: string }>("auth/token/", { method: "POST", body: JSON.stringify({ username, password }) });
  sessionStorage.setItem("fms_token", result.token);
  return result;
}
