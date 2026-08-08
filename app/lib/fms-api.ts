const API_URL = process.env.NEXT_PUBLIC_FMS_API_URL || "https://api-test.phloz.app/fms";

export const UNAUTHORISED_EVENT = "fms-unauthorised";

export function logout() {
  if (typeof window !== "undefined") sessionStorage.removeItem("fms_token");
}

// Shared by fmsRequest (JSON) and fmsRequestRaw (anything else - a file download,
// a response with headers the caller needs to read) so both get the same auth
// header, 401 handling and base URL.
async function fmsFetch(path: string, options: RequestInit = {}): Promise<Response> {
  if (!API_URL) throw new Error("NEXT_PUBLIC_FMS_API_URL is not configured");
  const token = typeof window !== "undefined" ? sessionStorage.getItem("fms_token") : null;
  // FormData needs the browser to set its own multipart boundary in Content-Type;
  // forcing application/json on a file upload breaks it, so only default to JSON
  // when the body isn't FormData.
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const response = await fetch(`${API_URL}/api/v1/${path.replace(/^\//, "")}`, {
    ...options,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Token ${token}` } : {}),
      ...options.headers,
    },
  });
  // A revoked token, or a login that has been deactivated, should return the operator to
  // the sign-in screen rather than leaving a workspace that can no longer load anything.
  if (response.status === 401 && token) {
    logout();
    window.dispatchEvent(new Event(UNAUTHORISED_EVENT));
    throw new Error("Your session has ended. Please sign in again.");
  }
  return response;
}

export async function fmsRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fmsFetch(path, options);
  if (!response.ok) throw new Error((await response.text()) || `API request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

// For endpoints that don't return JSON - a PDF preview, a file download - or where
// the caller needs response headers. Same auth/401 handling as fmsRequest, but
// hands back the raw Response instead of parsing it.
export async function fmsRequestRaw(path: string, options: RequestInit = {}): Promise<Response> {
  const response = await fmsFetch(path, options);
  if (!response.ok) throw new Error((await response.text().catch(() => "")) || `API request failed: ${response.status}`);
  return response;
}

export async function login(username: string, password: string) {
  const result = await fmsRequest<{ token: string }>("auth/token/", { method: "POST", body: JSON.stringify({ username, password }) });
  sessionStorage.setItem("fms_token", result.token);
  return result;
}
