"use client";

import { useEffect, useRef, useState } from "react";
import { fmsRequest, fmsRequestRaw, login, logout, UNAUTHORISED_EVENT } from "../lib/fms-api";

// Authenticated ADCOOP Voucher Portal. Unlike /vouchers (deliberately public),
// every call here goes through fmsRequest/fmsRequestRaw with the same
// fms_token session the rest of the console uses, and every screen is gated
// by the caller's role + department scope (see /access/me/).

type Department = { id: number; code: string; name: string };
type VoucherType = { id: number; code: string; name: string; department: number };
type Prefix = { id: number; prefix: string; label: string; department: number; voucher_type: number; sequence_length: number; next_sequence: number };
type Template = { id: number; name: string; artwork: string | null; is_default: boolean; is_active: boolean };
type Batch = {
  id: number; name: string; department: number; department_name: string; voucher_type_name: string; quantity: number;
  discount_type: string; display_value: string; currency: string; valid_from: string; valid_to: string;
  restrictions: string; terms: string; prefix_snapshot: string; status: string; combined_pdf_url: string;
  generation_error: string; created_by_username: string; approved_by_username: string; approved_at: string | null;
  rejection_reason: string; cancelled_at: string | null; generated_count: number; issued_count: number; created_at: string;
};
type Voucher = {
  id: number; batch: number; number: string; status: string; display_status: string;
  recipient_name: string; recipient_phone: string; recipient_email: string; recipient_reference: string;
  pdf_url: string; issued_at: string | null;
};
type Access = { role: string; actions: string[]; department_ids: number[] | null; is_django_staff: boolean };
type Notice = { id: number; batch: number; batch_name: string; kind: string; message: string; read_at: string | null; created_at: string };
type AccessGrant = { id: number; user: string; username: string; role: string; department_ids: number[]; department_names: string[]; is_active: boolean };

const EMPTY_FORM = {
  name: "", department: "", voucher_type: "", description: "", quantity: "10",
  discount_type: "fixed", percentage_value: "", max_discount_value: "", fixed_value: "", currency: "AED",
  valid_to: "", restrictions: "", terms: "", prefix: "",
};

const ROLE_LABELS: Record<string, string> = {
  administrator: "Administrator", requester: "Requester", approver: "Approver", report_viewer: "Report Viewer",
};

function statusClass(status: string) {
  const map: Record<string, string> = {
    draft: "open", pending_approval: "open", generating: "assigned", generated: "unassigned",
    partially_issued: "in-transit", fully_issued: "issued", rejected: "cancelled", failed: "expired",
  };
  return map[status] || status; // approved, cancelled, issued, expired already match CSS class names directly
}

function statusLabel(status: string) {
  return status.replaceAll("_", " ");
}

function unwrap<T>(data: { results: T[] } | T[]): T[] {
  return Array.isArray(data) ? data : data.results;
}

function parseApiError(error: any): string {
  const message = error?.message || "Something went wrong. Please try again.";
  try {
    const parsed = JSON.parse(message);
    if (typeof parsed === "string") return parsed;
    if (Array.isArray(parsed)) return String(parsed[0]);
    const first = Object.values(parsed)[0];
    return Array.isArray(first) ? String(first[0]) : String(first ?? message);
  } catch {
    return message;
  }
}

/** Fetches through fmsRequestRaw so the auth token goes with it, then saves the
 *  bytes locally. A plain <a href> can't carry the token, and the stored media
 *  URL is host-relative - it would resolve against this app's origin, not the
 *  API's, and land on the console's login screen. */
async function downloadBlob(path: string, filename: string): Promise<string | null> {
  try {
    const response = await fmsRequestRaw(path);
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoking synchronously can cancel the download before it starts.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
    return null;
  } catch (error: any) {
    return parseApiError(error);
  }
}

export default function VoucherPortal() {
  const [authenticated, setAuthenticated] = useState(false);
  const [checkedAuth, setCheckedAuth] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  useEffect(() => {
    setAuthenticated(!!(typeof window !== "undefined" && sessionStorage.getItem("fms_token")));
    setCheckedAuth(true);
    const onUnauthorised = () => setAuthenticated(false);
    window.addEventListener(UNAUTHORISED_EVENT, onUnauthorised);
    return () => window.removeEventListener(UNAUTHORISED_EVENT, onUnauthorised);
  }, []);

  const signIn = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoggingIn(true); setLoginError("");
    try { await login(username, password); setAuthenticated(true); }
    catch { setLoginError("Incorrect username or password."); }
    finally { setLoggingIn(false); }
  };

  const signOut = () => { logout(); setAuthenticated(false); };

  if (!checkedAuth) return null;

  if (!authenticated) {
    return <main className="login-page">
      <section className="login-card">
        <div className="brand login-brand"><span className="brand-mark">p</span><span>phloz</span></div>
        <p className="eyebrow">ADCOOP VOUCHER PORTAL</p>
        <h1>Sign in</h1>
        <p>Staff login required - this is separate from the public voucher desk.</p>
        <form onSubmit={signIn}>
          <label>Username<input value={username} onChange={e => setUsername(e.target.value)} required autoFocus /></label>
          <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} required /></label>
          {loginError && <div className="login-error">{loginError}</div>}
          <button className="primary" disabled={loggingIn}>{loggingIn ? "Signing in…" : "Sign in"}</button>
        </form>
      </section>
    </main>;
  }

  return <Portal onSignOut={signOut} />;
}

function Portal({ onSignOut }: { onSignOut: () => void }) {
  const [access, setAccess] = useState<Access | null>(null);
  const [screen, setScreen] = useState<"batches" | "reports" | "templates" | "access">("batches");

  const [departments, setDepartments] = useState<Department[]>([]);
  const [voucherTypes, setVoucherTypes] = useState<VoucherType[]>([]);
  const [prefixes, setPrefixes] = useState<Prefix[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loadingBatches, setLoadingBatches] = useState(false);

  const [notifications, setNotifications] = useState<Notice[]>([]);
  const [showNotifications, setShowNotifications] = useState(false);

  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [previewHash, setPreviewHash] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const [artworkPreviewUrl, setArtworkPreviewUrl] = useState("");
  const [templateId, setTemplateId] = useState<number | "">("");
  const [artworkUploading, setArtworkUploading] = useState(false);
  const [artworkError, setArtworkError] = useState("");

  const [activeBatch, setActiveBatch] = useState<Batch | null>(null);
  const [vouchers, setVouchers] = useState<Voucher[]>([]);
  const [voucherStatus, setVoucherStatus] = useState("");
  const [issuingId, setIssuingId] = useState<number | null>(null);
  const [issuePhone, setIssuePhone] = useState("");
  const [issueError, setIssueError] = useState("");
  const [csvBusy, setCsvBusy] = useState(false);
  const [csvResult, setCsvResult] = useState<{ assigned: number; remaining_available: number; rejected: any[] } | null>(null);
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowError, setWorkflowError] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [showReject, setShowReject] = useState(false);
  const [cancelReason, setCancelReason] = useState("");
  const [showCancel, setShowCancel] = useState(false);

  const loadAccess = async () => setAccess(await fmsRequest<Access>("voucher-portal/access/me/"));

  const loadReferenceData = async () => {
    const [d, t, p] = await Promise.all([
      fmsRequest<{ results: Department[] } | Department[]>("voucher-portal/departments/?page_size=200"),
      fmsRequest<{ results: VoucherType[] } | VoucherType[]>("voucher-portal/voucher-types/?page_size=200"),
      fmsRequest<{ results: Prefix[] } | Prefix[]>("voucher-portal/prefixes/?page_size=200"),
    ]);
    setDepartments(unwrap(d)); setVoucherTypes(unwrap(t)); setPrefixes(unwrap(p));
  };

  const loadBatches = async () => {
    setLoadingBatches(true);
    try {
      const data = await fmsRequest<{ results: Batch[] } | Batch[]>("voucher-portal/batches/?page_size=100");
      const list = unwrap(data);
      setBatches(list);
      setActiveBatch(current => current ? list.find(b => b.id === current.id) || current : current);
    } finally { setLoadingBatches(false); }
  };

  const loadNotifications = async () => {
    const data = await fmsRequest<{ results: Notice[] } | Notice[]>("voucher-portal/notifications/?page_size=50");
    setNotifications(unwrap(data));
  };

  useEffect(() => { loadAccess(); loadReferenceData(); loadBatches(); loadNotifications(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // While a batch is still assembling PDFs in the background, poll until it's ready -
  // including the open batch's voucher list, since per-voucher pdf_url fills in
  // progressively as each one renders.
  useEffect(() => {
    if (!batches.some(b => b.status === "generating")) return;
    const timer = setInterval(() => {
      loadBatches();
      if (activeBatch?.status === "generating") loadVouchers(activeBatch.id, voucherStatus);
    }, 2500);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batches]);

  const loadVouchers = async (batchId: number, status = "") => {
    const params = new URLSearchParams({ batch: String(batchId), page_size: "200" });
    if (status) params.set("status", status);
    const data = await fmsRequest<{ results: Voucher[] } | Voucher[]>(`voucher-portal/vouchers/?${params.toString()}`);
    setVouchers(unwrap(data));
  };

  const openBatch = async (batch: Batch) => {
    setActiveBatch(batch); setVoucherStatus(""); setCsvResult(null); setWorkflowError("");
    setShowReject(false); setShowCancel(false);
    await loadVouchers(batch.id);
  };

  const resetCreateForm = () => {
    setForm(EMPTY_FORM); setPreviewHash(""); setPreviewUrl(""); setPreviewError(""); setCreateError("");
    setArtworkPreviewUrl(""); setTemplateId(""); setArtworkError("");
  };

  const departmentPrefixes = prefixes.filter(p => !form.department || String(p.department) === form.department);
  const departmentTypes = voucherTypes.filter(t => !form.department || String(t.department) === form.department);
  const visibleDepartments = access?.department_ids
    ? departments.filter(d => access.department_ids!.includes(d.id))
    : departments;

  const updateForm = (patch: Partial<typeof form>) => {
    setForm(f => ({ ...f, ...patch }));
    setPreviewHash(""); setPreviewUrl(""); // any change invalidates the preview
  };

  const uploadArtwork = async (file: File) => {
    setArtworkUploading(true); setArtworkError("");
    setArtworkPreviewUrl(URL.createObjectURL(file));
    setPreviewHash(""); setPreviewUrl(""); // new artwork invalidates any existing preview
    try {
      const body = new FormData();
      body.append("name", `${form.name || "Custom"} artwork`);
      body.append("artwork", file);
      const template = await fmsRequest<{ id: number }>("voucher-portal/templates/", { method: "POST", body });
      setTemplateId(template.id);
    } catch (error: any) {
      setArtworkError(parseApiError(error));
      setArtworkPreviewUrl(""); setTemplateId("");
    } finally { setArtworkUploading(false); }
  };

  const clearArtwork = () => { setArtworkPreviewUrl(""); setTemplateId(""); setArtworkError(""); setPreviewHash(""); setPreviewUrl(""); };

  const buildPayload = () => ({
    name: form.name, department: form.department, voucher_type: form.voucher_type, description: form.description,
    quantity: form.quantity, discount_type: form.discount_type,
    percentage_value: form.discount_type === "percentage" ? form.percentage_value : undefined,
    max_discount_value: form.discount_type === "percentage" ? (form.max_discount_value || undefined) : undefined,
    fixed_value: form.discount_type === "fixed" ? form.fixed_value : undefined,
    currency: form.currency, valid_to: form.valid_to,
    restrictions: form.restrictions, terms: form.terms, prefix: form.prefix,
    template: templateId || undefined,
  });

  const runPreview = async () => {
    setPreviewing(true); setPreviewError(""); setPreviewUrl("");
    try {
      const response = await fmsRequestRaw("voucher-portal/batches/preview/", { method: "POST", body: JSON.stringify(buildPayload()) });
      const hash = response.headers.get("X-Preview-Hash") || "";
      const blob = await response.blob();
      setPreviewUrl(URL.createObjectURL(blob));
      setPreviewHash(hash);
    } catch (error: any) {
      setPreviewError(parseApiError(error));
    } finally { setPreviewing(false); }
  };

  const saveDraft = async () => {
    setCreating(true); setCreateError("");
    try {
      await fmsRequest("voucher-portal/batches/", { method: "POST", body: JSON.stringify({ ...buildPayload(), preview_hash: previewHash }) });
      setShowCreate(false); resetCreateForm(); loadBatches();
    } catch (error: any) {
      setCreateError(parseApiError(error));
    } finally { setCreating(false); }
  };

  const runWorkflowAction = async (action: string, body?: any) => {
    if (!activeBatch) return;
    setWorkflowBusy(true); setWorkflowError("");
    try {
      await fmsRequest(`voucher-portal/batches/${activeBatch.id}/${action}/`, { method: "POST", body: JSON.stringify(body || {}) });
      setShowReject(false); setShowCancel(false); setRejectReason(""); setCancelReason("");
      await Promise.all([loadBatches(), loadVouchers(activeBatch.id, voucherStatus)]);
    } catch (error: any) {
      setWorkflowError(parseApiError(error));
    } finally { setWorkflowBusy(false); }
  };

  const startIssue = (voucher: Voucher) => { setIssuingId(voucher.id); setIssuePhone(""); setIssueError(""); };

  const submitIssue = async (voucher: Voucher) => {
    setIssueError("");
    try {
      await fmsRequest("voucher-portal/vouchers/issue/", { method: "POST", body: JSON.stringify({ voucher_ids: [voucher.id], phone: issuePhone }) });
      setIssuingId(null);
      if (activeBatch) { loadVouchers(activeBatch.id, voucherStatus); loadBatches(); }
    } catch (error: any) {
      setIssueError(parseApiError(error));
    }
  };

  const redeemVoucher = async (voucher: Voucher) => {
    try {
      await fmsRequest(`voucher-portal/vouchers/${voucher.id}/redeem/`, { method: "POST", body: "{}" });
      if (activeBatch) loadVouchers(activeBatch.id, voucherStatus);
    } catch (error: any) { setIssueError(parseApiError(error)); }
  };

  const cancelVoucher = async (voucher: Voucher) => {
    try {
      await fmsRequest(`voucher-portal/vouchers/${voucher.id}/cancel/`, { method: "POST", body: "{}" });
      if (activeBatch) { loadVouchers(activeBatch.id, voucherStatus); loadBatches(); }
    } catch (error: any) { setIssueError(parseApiError(error)); }
  };

  const uploadCsv = async (file: File) => {
    if (!activeBatch) return;
    setCsvBusy(true); setCsvResult(null);
    try {
      const body = new FormData(); body.append("file", file);
      const result = await fmsRequest<{ assigned: number; remaining_available: number; rejected: any[] }>(
        `voucher-portal/batches/${activeBatch.id}/issue_bulk/`, { method: "POST", body });
      setCsvResult(result);
      loadVouchers(activeBatch.id, voucherStatus); loadBatches();
    } catch (error: any) {
      setCsvResult({ assigned: 0, remaining_available: 0, rejected: [{ errors: parseApiError(error) }] });
    } finally { setCsvBusy(false); }
  };

  const unreadCount = notifications.filter(n => !n.read_at).length;
  const markNotificationRead = async (notice: Notice) => {
    if (notice.read_at) return;
    await fmsRequest(`voucher-portal/notifications/${notice.id}/read/`, { method: "POST", body: "{}" });
    loadNotifications();
  };
  const markAllRead = async () => {
    await fmsRequest("voucher-portal/notifications/read-all/", { method: "POST", body: "{}" });
    loadNotifications();
  };

  if (!access) return <main className="voucher-page"><div className="data-state">Loading…</div></main>;

  const nav = <header className="voucher-topbar">
    <div>
      <p className="voucher-eyebrow">{ROLE_LABELS[access.role] || "STAFF"}</p>
      <h1>Voucher Portal</h1>
    </div>
    <nav className="voucher-nav">
      <button type="button" className={"link-button" + (screen === "batches" ? " active" : "")} onClick={() => { setScreen("batches"); setActiveBatch(null); }}>Batches</button>
      {access.actions.includes("report") && <button type="button" className={"link-button" + (screen === "reports" ? " active" : "")} onClick={() => setScreen("reports")}>Reports</button>}
      {(access.actions.includes("admin") || access.actions.includes("create")) && <button type="button" className={"link-button" + (screen === "templates" ? " active" : "")} onClick={() => setScreen("templates")}>Templates</button>}
      {access.actions.includes("admin") && <button type="button" className={"link-button" + (screen === "access" ? " active" : "")} onClick={() => setScreen("access")}>Team access</button>}
    </nav>
    <div style={{ marginLeft: "auto", display: "flex", gap: 10, alignItems: "center" }}>
      <div className="voucher-notif-wrap">
        <button type="button" className="secondary voucher-notif-bell" onClick={() => setShowNotifications(v => !v)}>
          Notifications{unreadCount > 0 && <span className="voucher-notif-badge">{unreadCount}</span>}
        </button>
        {showNotifications && <div className="voucher-notif-dropdown">
          <div className="voucher-notif-head">
            <strong>Notifications</strong>
            {unreadCount > 0 && <button type="button" className="link-button" onClick={markAllRead}>Mark all read</button>}
          </div>
          {notifications.length === 0 && <div className="data-state">Nothing yet.</div>}
          {notifications.map(n => <div key={n.id} className={"voucher-notif-item" + (n.read_at ? "" : " unread")} onClick={() => markNotificationRead(n)}>
            <span>{n.message}</span>
            <time>{new Date(n.created_at).toLocaleString()}</time>
          </div>)}
        </div>}
      </div>
      {screen === "batches" && !activeBatch && access.actions.includes("create") &&
        <button className="primary" onClick={() => { setShowCreate(v => !v); resetCreateForm(); }}>{showCreate ? "Close" : "New batch"}</button>}
      <button className="secondary" onClick={onSignOut}>Sign out</button>
    </div>
  </header>;

  if (screen === "reports") {
    return <main className="voucher-page">{nav}<ReportsScreen departments={departments} voucherTypes={voucherTypes} /></main>;
  }
  if (screen === "templates") {
    return <main className="voucher-page">{nav}<TemplatesScreen canAdmin={access.actions.includes("admin")} /></main>;
  }
  if (screen === "access") {
    return <main className="voucher-page">{nav}<AccessScreen departments={departments} /></main>;
  }

  if (activeBatch) {
    const canApprove = access.actions.includes("approve") && (activeBatch.created_by_username !== undefined);
    const isOwnRequest = false; // server enforces the real self-approval check; this only hides the button as a hint
    return <main className="voucher-page">
      {nav}

      <section className="voucher-card">
        <div className="voucher-card-head">
          <h2>{activeBatch.name} · {activeBatch.display_value} · {activeBatch.prefix_snapshot}</h2>
          <span className={"status " + statusClass(activeBatch.status)}>{statusLabel(activeBatch.status)}</span>
        </div>
        <p style={{ fontSize: 12, color: "var(--voucher-muted)" }}>
          {activeBatch.department_name} · {activeBatch.voucher_type_name} · {activeBatch.quantity} vouchers ·
          Valid until {activeBatch.valid_to} · {activeBatch.issued_count} issued
        </p>
        {activeBatch.created_by_username && <p style={{ fontSize: 11, color: "var(--voucher-muted)" }}>Requested by {activeBatch.created_by_username}</p>}
        {activeBatch.approved_by_username && <p style={{ fontSize: 11, color: "var(--voucher-muted)" }}>Approved by {activeBatch.approved_by_username}{activeBatch.approved_at ? ` · ${new Date(activeBatch.approved_at).toLocaleString()}` : ""}</p>}
        {activeBatch.rejection_reason && <div className="form-error">Rejected: {activeBatch.rejection_reason}</div>}
        {activeBatch.status === "generating" && <div className="data-state">Assembling PDFs in the background — this refreshes automatically.</div>}
        {activeBatch.status === "failed" && <div className="data-state error">Generation failed: {activeBatch.generation_error}</div>}
        {/* Downloaded through the authenticated API, not linked to a media path:
            a plain <a href> sends no auth token and the stored URL is host-relative,
            so the browser would resolve it against this app's own origin. */}
        {activeBatch.combined_pdf_url && <button type="button" className="secondary voucher-download-link"
          onClick={async () => setWorkflowError(await downloadBlob(
            `voucher-portal/batches/${activeBatch.id}/download/`,
            `${activeBatch.prefix_snapshot || "batch"}-${activeBatch.id}-vouchers.pdf`) || "")}>
          Download print-ready PDF (all vouchers)</button>}

        <div className="voucher-workflow-actions">
          {activeBatch.status === "draft" && access.actions.includes("create") &&
            <button type="button" className="primary" disabled={workflowBusy} onClick={() => runWorkflowAction("submit")}>Submit for approval</button>}
          {activeBatch.status === "rejected" && access.actions.includes("create") &&
            <button type="button" className="primary" disabled={workflowBusy} onClick={() => runWorkflowAction("submit")}>Resubmit for approval</button>}
          {activeBatch.status === "pending_approval" && access.actions.includes("approve") && <>
            <button type="button" className="primary" disabled={workflowBusy} onClick={() => runWorkflowAction("approve")}>Approve</button>
            <button type="button" className="secondary" disabled={workflowBusy} onClick={() => setShowReject(v => !v)}>Reject</button>
          </>}
          {activeBatch.status === "approved" && access.actions.includes("create") &&
            <button type="button" className="primary" disabled={workflowBusy} onClick={() => runWorkflowAction("generate")}>Generate vouchers</button>}
          {!["cancelled", "rejected", "fully_issued"].includes(activeBatch.status) && access.actions.includes("admin") &&
            <button type="button" className="secondary" disabled={workflowBusy} onClick={() => setShowCancel(v => !v)}>Cancel batch</button>}
        </div>

        {showReject && <div className="voucher-inline-form">
          <textarea rows={2} placeholder="Reason for rejection (required)" value={rejectReason} onChange={e => setRejectReason(e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="primary" disabled={workflowBusy || !rejectReason.trim()} onClick={() => runWorkflowAction("reject", { reason: rejectReason })}>Confirm rejection</button>
            <button type="button" className="link-button" onClick={() => setShowReject(false)}>Cancel</button>
          </div>
        </div>}
        {showCancel && <div className="voucher-inline-form">
          <textarea rows={2} placeholder="Reason (optional)" value={cancelReason} onChange={e => setCancelReason(e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="primary" disabled={workflowBusy} onClick={() => runWorkflowAction("cancel", { reason: cancelReason })}>Confirm cancellation</button>
            <button type="button" className="link-button" onClick={() => setShowCancel(false)}>Back</button>
          </div>
        </div>}
        {workflowError && <div className="form-error" style={{ marginTop: 10 }}>{workflowError}</div>}
      </section>

      {["generating", "generated", "partially_issued", "fully_issued"].includes(activeBatch.status) && <>
        {access.actions.includes("issue") && <section className="voucher-card">
          <div className="voucher-card-head"><h2>Issue recipients</h2></div>
          <label style={{ display: "block", fontSize: 11, fontWeight: 700, color: "var(--voucher-muted)", marginBottom: 8 }}>
            Bulk upload (CSV: name,phone,email,reference)
            <input type="file" accept=".csv" disabled={csvBusy}
              onChange={e => e.target.files?.[0] && uploadCsv(e.target.files[0])} style={{ display: "block", marginTop: 6 }} />
          </label>
          {csvBusy && <div className="data-state">Uploading…</div>}
          {csvResult && <div className={csvResult.assigned ? "voucher-success" : "form-error"}>
            {csvResult.assigned ? `Assigned ${csvResult.assigned} voucher(s). ${csvResult.remaining_available} remain available.` : "Upload failed."}
            {csvResult.rejected.length > 0 && <div>{csvResult.rejected.length} row(s) rejected.</div>}
          </div>}
        </section>}

        <section className="voucher-card">
          <div className="voucher-card-head"><h2>Vouchers</h2></div>
          <div className="voucher-tabs">
            {["", "generated", "issued", "redeemed", "cancelled", "expired"].map(s => (
              <button key={s} type="button" className={"chip" + (voucherStatus === s ? " active" : "")}
                onClick={() => { setVoucherStatus(s); loadVouchers(activeBatch.id, s); }}>{s || "All"}</button>
            ))}
          </div>
          {issueError && <div className="form-error" style={{ marginBottom: 10 }}>{issueError}</div>}
          <div className="table-wrap">
            <table>
              <thead><tr><th>Number</th><th>Recipient</th><th>Status</th><th>Actions</th></tr></thead>
              <tbody>
                {vouchers.map(v => <tr key={v.id}>
                  <td><strong>{v.number}</strong></td>
                  <td>{v.recipient_name || v.recipient_phone || v.recipient_email || "—"}</td>
                  <td><span className={"status " + statusClass(v.display_status)}>{statusLabel(v.display_status)}</span></td>
                  <td className="voucher-actions">
                    {issuingId === v.id ? <div className="voucher-issue-inline">
                      <input value={issuePhone} onChange={e => setIssuePhone(e.target.value)} placeholder="Phone (optional)" autoFocus />
                      <button type="button" className="primary" onClick={() => submitIssue(v)}>Confirm</button>
                      <button type="button" className="link-button" onClick={() => setIssuingId(null)}>Cancel</button>
                    </div> : <>
                      {v.display_status === "generated" && access.actions.includes("issue") && <button type="button" className="link-button" onClick={() => startIssue(v)}>Issue</button>}
                      {v.display_status === "issued" && access.actions.includes("issue") && <button type="button" className="link-button" onClick={() => redeemVoucher(v)}>Redeem</button>}
                      {!["cancelled", "redeemed"].includes(v.status) && access.actions.includes("admin") && <button type="button" className="link-button" onClick={() => cancelVoucher(v)}>Cancel</button>}
                      {v.pdf_url && <button type="button" className="link-button"
                        onClick={async () => setIssueError(await downloadBlob(`voucher-portal/vouchers/${v.id}/download/`, `${v.number}.pdf`) || "")}>PDF</button>}
                    </>}
                  </td>
                </tr>)}
                {vouchers.length === 0 && <tr><td colSpan={4}><div className="data-state">No vouchers in this view.</div></td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      </>}
    </main>;
  }

  return <main className="voucher-page">
    {nav}

    {showCreate && <section className="voucher-card">
      <div className="voucher-card-head"><h2>Create a voucher batch</h2></div>
      <form className="voucher-form" onSubmit={e => e.preventDefault()}>
        <label>Voucher name<input required value={form.name} onChange={e => updateForm({ name: e.target.value })} /></label>
        <label>Quantity<input required type="number" min={1} max={10000} value={form.quantity} onChange={e => updateForm({ quantity: e.target.value })} /></label>

        <label>Department<select required value={form.department} onChange={e => updateForm({ department: e.target.value, voucher_type: "", prefix: "" })}>
          <option value="">Select…</option>
          {visibleDepartments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select></label>
        <label>Voucher type<select required value={form.voucher_type} onChange={e => updateForm({ voucher_type: e.target.value })}>
          <option value="">Select…</option>
          {departmentTypes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select></label>

        <label>Prefix<select required value={form.prefix} onChange={e => updateForm({ prefix: e.target.value })}>
          <option value="">Select…</option>
          {departmentPrefixes.map(p => <option key={p.id} value={p.id}>{p.prefix} — {p.label}</option>)}
        </select></label>
        <label>Currency<input value={form.currency} onChange={e => updateForm({ currency: e.target.value.toUpperCase() })} maxLength={8} /></label>

        <label className="voucher-form-wide">Description <small>(optional)</small>
          <textarea rows={2} value={form.description} onChange={e => updateForm({ description: e.target.value })} />
        </label>

        <label>Discount type<select value={form.discount_type} onChange={e => updateForm({ discount_type: e.target.value })}>
          <option value="fixed">Fixed amount</option>
          <option value="percentage">Percentage</option>
        </select></label>
        {form.discount_type === "percentage" ? <>
          <label>Percentage (0–100)<input required type="number" min={0.01} max={100} step={0.01} value={form.percentage_value}
            onChange={e => updateForm({ percentage_value: e.target.value })} /></label>
          <label className="voucher-form-wide">Maximum discount <small>(optional — "up to {form.currency} X")</small>
            <input type="number" min={0} step={0.01} value={form.max_discount_value} onChange={e => updateForm({ max_discount_value: e.target.value })} />
          </label>
        </> : <label>Fixed value<input required type="number" min={0.01} step={0.01} value={form.fixed_value}
          onChange={e => updateForm({ fixed_value: e.target.value })} /></label>}

        <label className="voucher-form-wide">Valid until<input required type="date" value={form.valid_to} onChange={e => updateForm({ valid_to: e.target.value })} /></label>

        <label className="voucher-form-wide">Restrictions <small>(optional)</small>
          <textarea rows={2} value={form.restrictions} onChange={e => updateForm({ restrictions: e.target.value })} />
        </label>
        <label className="voucher-form-wide">Terms and conditions
          <textarea rows={3} value={form.terms} onChange={e => updateForm({ terms: e.target.value })} />
        </label>

        <label className="voucher-form-wide">Voucher artwork <small>(optional — uses the default ADCOOP design if not provided; JPEG or PNG, 2.74:1, 1500–4000px wide, up to 5 MB)</small>
          <input type="file" accept="image/png,image/jpeg" disabled={artworkUploading}
            onChange={e => e.target.files?.[0] && uploadArtwork(e.target.files[0])} />
        </label>
        {artworkUploading && <div className="data-state voucher-form-wide">Uploading…</div>}
        {artworkError && <div className="form-error voucher-form-wide">{artworkError}</div>}
        {artworkPreviewUrl && !artworkUploading && <div className="voucher-form-wide voucher-artwork-preview">
          <img src={artworkPreviewUrl} alt="Uploaded artwork" />
          <button type="button" className="link-button" onClick={clearArtwork}>Remove — use default design</button>
        </div>}

        {previewError && <div className="form-error voucher-form-wide">{previewError}</div>}
        <button type="button" className="secondary voucher-form-wide" disabled={previewing} onClick={runPreview}>
          {previewing ? "Rendering…" : "Preview"}
        </button>
      </form>

      {previewUrl && <div style={{ marginTop: 16 }}>
        <iframe src={previewUrl} title="Voucher preview" style={{ width: "100%", height: 420, border: "1px solid var(--voucher-line)", borderRadius: 10 }} />
        <p style={{ fontSize: 11, color: "var(--voucher-muted)", margin: "10px 0" }}>
          Review the design above. Changing any field above invalidates this preview. Saving creates a draft — it still needs to be submitted for approval.
        </p>
        {createError && <div className="form-error">{createError}</div>}
        <button className="primary" disabled={creating} onClick={saveDraft} style={{ width: "100%", height: 42 }}>
          {creating ? "Saving…" : `Save as draft (${form.quantity} voucher${Number(form.quantity) === 1 ? "" : "s"})`}
        </button>
      </div>}
    </section>}

    <section className="voucher-card">
      <div className="voucher-card-head"><h2>Batches</h2></div>
      {loadingBatches && <div className="data-state">Loading…</div>}
      {!loadingBatches && batches.length === 0 && <div className="data-state">No batches yet.</div>}
      {!loadingBatches && batches.length > 0 && <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Department</th><th>Type</th><th>Value</th><th>Qty</th><th>Status</th><th>Issued</th><th></th></tr></thead>
          <tbody>
            {batches.map(b => <tr key={b.id} style={{ cursor: "pointer" }} onClick={() => openBatch(b)}>
              <td><strong>{b.name}</strong><small>{b.prefix_snapshot}</small></td>
              <td>{b.department_name}</td>
              <td>{b.voucher_type_name}</td>
              <td>{b.display_value}</td>
              <td>{b.generated_count || b.quantity}</td>
              <td><span className={"status " + statusClass(b.status)}>{statusLabel(b.status)}</span></td>
              <td>{b.issued_count}</td>
              <td><button type="button" className="link-button" onClick={e => { e.stopPropagation(); openBatch(b); }}>Open</button></td>
            </tr>)}
          </tbody>
        </table>
      </div>}
    </section>
  </main>;
}

// Three measures shown across both charts. Colours are the first three slots of
// the validated categorical palette (checked all-pairs against this page's white
// card surface: worst CVD ΔE 9.2, worst normal-vision ΔE 24.0). Aqua sits under
// 3:1 on white, so both charts ship direct labels and a table view - the relief
// the contrast warning requires. Identity follows the measure, never its rank,
// so filtering never repaints a series.
const SERIES = [
  { key: "created", label: "Created", color: "#2a78d6" },
  { key: "issued", label: "Issued", color: "#eb6834" },
  { key: "redeemed", label: "Redeemed", color: "#1baf7a" },
] as const;

const CHART_INK = "#231B36";
const CHART_MUTED = "#6B6480";
const CHART_GRID = "#E6E1F0";
const CHART_AXIS = "#CFC7DF";

/** Clean axis steps only (1 / 2 / 5 / 10 × a power of ten). A 2.5 step would
 *  round to a label that doesn't sit where the gridline is - "3" drawn at 2.5. */
function niceTicks(max: number, count = 4) {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = Math.max(1, [1, 2, 5, 10].map(m => m * magnitude).find(s => s >= raw) || magnitude * 10);
  const ticks: number[] = [];
  for (let value = 0; value <= max + step / 2; value += step) ticks.push(value);
  return ticks;
}

/** Rounded at the data end, square at the baseline (per the mark spec). */
function barPath(x: number, y: number, w: number, h: number, radius = 4) {
  if (w <= 0.5) return "";
  const r = Math.min(radius, w, h / 2);
  return `M${x},${y} H${x + w - r} A${r},${r} 0 0 1 ${x + w},${y + r} V${y + h - r} A${r},${r} 0 0 1 ${x + w - r},${y + h} H${x} Z`;
}

function ChartLegend() {
  return <ul className="viz-legend">
    {SERIES.map(s => <li key={s.key}><i style={{ background: s.color }} />{s.label}</li>)}
  </ul>;
}

function TrendChart({ data }: { data: any[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 760, H = 268;
  const padding = { top: 18, right: 64, bottom: 38, left: 46 };
  const plotW = W - padding.left - padding.right;
  const plotH = H - padding.top - padding.bottom;

  const max = Math.max(1, ...data.flatMap(d => SERIES.map(s => d[s.key] as number)));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1];
  const x = (i: number) => padding.left + (data.length <= 1 ? plotW / 2 : (i * plotW) / (data.length - 1));
  const y = (value: number) => padding.top + plotH - (value / top) * plotH;

  // Direct end-labels, but only where they don't collide. Converging series drop
  // their label to the legend + tooltip rather than being nudged apart, which
  // would detach the label from its line.
  const endLabels = SERIES
    .map(s => ({ ...s, value: data.length ? (data[data.length - 1][s.key] as number) : 0 }))
    .map(s => ({ ...s, y: y(s.value) }))
    .sort((a, b) => a.y - b.y)
    .reduce<{ key: string; label: string; color: string; value: number; y: number }[]>((kept, entry) => {
      if (kept.length && entry.y - kept[kept.length - 1].y < 13) return kept;
      kept.push(entry); return kept;
    }, []);

  const active = hover !== null ? data[hover] : null;

  return <div className="viz-wrap">
    <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg" role="img"
         aria-label={`Vouchers created, issued and redeemed per month over the last ${data.length} months`}>
      {ticks.map(tick => <g key={tick}>
        <line x1={padding.left} x2={padding.left + plotW} y1={y(tick)} y2={y(tick)}
              stroke={tick === 0 ? CHART_AXIS : CHART_GRID} strokeWidth="1" />
        <text x={padding.left - 10} y={y(tick) + 3.5} textAnchor="end" fontSize="10" fill={CHART_MUTED}
              style={{ fontVariantNumeric: "tabular-nums" }}>{tick.toLocaleString()}</text>
      </g>)}

      {data.map((row, i) => {
        // Label every other month once the series gets long, so ticks never collide.
        const show = data.length <= 8 || i % 2 === data.length % 2;
        if (!show) return null;
        const [year, month] = row.month.split("-");
        const name = new Date(Number(year), Number(month) - 1, 1).toLocaleString("en", { month: "short" });
        return <text key={row.month} x={x(i)} y={H - 16} textAnchor="middle" fontSize="10" fill={CHART_MUTED}>
          {month === "01" ? `${name} ${year.slice(2)}` : name}
        </text>;
      })}

      {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={padding.top} y2={padding.top + plotH}
                               stroke={CHART_AXIS} strokeWidth="1" />}

      {SERIES.map(s => (
        <path key={s.key} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"
              d={data.map((row, i) => `${i ? "L" : "M"}${x(i)},${y(row[s.key])}`).join(" ")} />
      ))}

      {/* End markers carry a 2px surface ring so they stay legible where lines cross. */}
      {data.length > 0 && SERIES.map(s => (
        <circle key={s.key} cx={x(data.length - 1)} cy={y(data[data.length - 1][s.key])} r="4.5"
                fill={s.color} stroke="#fff" strokeWidth="2" />
      ))}
      {hover !== null && SERIES.map(s => (
        <circle key={s.key} cx={x(hover)} cy={y(data[hover][s.key])} r="4.5" fill={s.color} stroke="#fff" strokeWidth="2" />
      ))}

      {endLabels.map(s => (
        <text key={s.key} x={x(data.length - 1) + 10} y={s.y + 3.5} fontSize="11" fontWeight="600" fill={CHART_INK}
              style={{ fontVariantNumeric: "tabular-nums" }}>{s.value.toLocaleString()}</text>
      ))}

      {/* Hit targets span the full column height, not just the 9px marker. */}
      {data.map((row, i) => (
        <rect key={row.month} x={x(i) - plotW / Math.max(data.length, 1) / 2} y={padding.top}
              width={plotW / Math.max(data.length, 1)} height={plotH} fill="transparent"
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
      ))}
    </svg>

    {active && <div className="viz-tooltip" style={{ left: `${(x(hover!) / W) * 100}%` }}>
      <strong>{active.month}</strong>
      {SERIES.map(s => <span key={s.key}><i style={{ background: s.color }} />{s.label}<b>{active[s.key].toLocaleString()}</b></span>)}
    </div>}
  </div>;
}

function DepartmentChart({ rows }: { rows: { name: string; created: number; issued: number; redeemed: number }[] }) {
  const [hover, setHover] = useState<string | null>(null);
  const W = 760;
  const BAR = 14, BAR_GAP = 2, BAND_GAP = 26, LABEL_W = 150;
  const bandH = SERIES.length * BAR + (SERIES.length - 1) * BAR_GAP;
  const H = rows.length * bandH + Math.max(0, rows.length - 1) * BAND_GAP + 46;
  const plotW = W - LABEL_W - 66;
  const max = Math.max(1, ...rows.flatMap(r => SERIES.map(s => r[s.key] as number)));
  const ticks = niceTicks(max, 3);
  const top = ticks[ticks.length - 1];
  const scale = (value: number) => (value / top) * plotW;

  return <div className="viz-wrap">
    <svg viewBox={`0 0 ${W} ${H}`} className="viz-svg" role="img"
         aria-label="Vouchers created, issued and redeemed by department">
      {ticks.map(tick => <g key={tick}>
        <line x1={LABEL_W + scale(tick)} x2={LABEL_W + scale(tick)} y1={0} y2={H - 30}
              stroke={tick === 0 ? CHART_AXIS : CHART_GRID} strokeWidth="1" />
        <text x={LABEL_W + scale(tick)} y={H - 12} textAnchor="middle" fontSize="10" fill={CHART_MUTED}
              style={{ fontVariantNumeric: "tabular-nums" }}>{tick.toLocaleString()}</text>
      </g>)}

      {rows.map((row, index) => {
        const bandTop = index * (bandH + BAND_GAP);
        return <g key={row.name} onMouseEnter={() => setHover(row.name)} onMouseLeave={() => setHover(null)}>
          <rect x="0" y={bandTop - BAND_GAP / 2} width={W} height={bandH + BAND_GAP} fill="transparent" />
          <text x={LABEL_W - 14} y={bandTop + bandH / 2 + 4} textAnchor="end" fontSize="11.5"
                fontWeight={hover === row.name ? 700 : 500} fill={CHART_INK}>{row.name}</text>
          {SERIES.map((s, seriesIndex) => {
            const value = row[s.key] as number;
            const barY = bandTop + seriesIndex * (BAR + BAR_GAP);
            return <g key={s.key}>
              <path d={barPath(LABEL_W, barY, scale(value), BAR)} fill={s.color} />
              <text x={LABEL_W + scale(value) + 8} y={barY + BAR / 2 + 3.5} fontSize="10.5" fill={CHART_INK}
                    style={{ fontVariantNumeric: "tabular-nums" }}>{value.toLocaleString()}</text>
            </g>;
          })}
        </g>;
      })}
    </svg>
  </div>;
}

function ChartOrTable({ title, action, chart, table }: { title: string; action?: React.ReactNode; chart: React.ReactNode; table: React.ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return <section className="voucher-card">
    <div className="voucher-card-head">
      <h2>{title}</h2>
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        {!asTable && <ChartLegend />}
        <button type="button" className="link-button" onClick={() => setAsTable(v => !v)}>
          {asTable ? "Show chart" : "Show table"}
        </button>
        {action}
      </div>
    </div>
    {asTable ? table : chart}
  </section>;
}

function ReportsScreen({ departments, voucherTypes }: { departments: Department[]; voucherTypes: VoucherType[] }) {
  const [filters, setFilters] = useState({ department: "", voucher_type: "", status: "", from: "", to: "" });
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [byDepartment, setByDepartment] = useState<any[]>([]);
  const [byType, setByType] = useState<any[]>([]);
  const [trend, setTrend] = useState<any[]>([]);
  const [batchRows, setBatchRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const query = () => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params.toString() ? `?${params.toString()}` : "";
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setRefreshing(true);
      const suffix = query();
      const [s, d, t, tr, b] = await Promise.all([
        fmsRequest<Record<string, number>>(`voucher-portal/reports/summary/${suffix}`),
        fmsRequest<any[]>(`voucher-portal/reports/by-department/${suffix}`),
        fmsRequest<any[]>(`voucher-portal/reports/by-type/${suffix}`),
        fmsRequest<any[]>(`voucher-portal/reports/trend/${suffix}`),
        fmsRequest<any[]>(`voucher-portal/reports/batches/${suffix}`),
      ]);
      if (cancelled) return;
      setSummary(s); setByDepartment(d); setByType(t); setTrend(tr); setBatchRows(b);
      setLoading(false); setRefreshing(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  if (loading || !summary) return <div className="data-state">Loading…</div>;

  const departmentSeries = byDepartment.map(row => ({
    name: row.batch__department__name, created: row.total, issued: row.issued, redeemed: row.redeemed,
  }));

  const breakdownTable = (rows: any[], idKey: string, nameKey: string, heading: string) => (
    <div className="table-wrap">
      <table>
        <thead><tr><th>{heading}</th><th>Created</th><th>Issued</th><th>Redeemed</th></tr></thead>
        <tbody>
          {rows.map(row => <tr key={row[idKey]}>
            <td>{row[nameKey]}</td><td>{row.total}</td><td>{row.issued}</td><td>{row.redeemed}</td>
          </tr>)}
          {rows.length === 0 && <tr><td colSpan={4}><div className="data-state">Nothing in this slice.</div></td></tr>}
        </tbody>
      </table>
    </div>
  );

  return <div style={{ opacity: refreshing ? 0.6 : 1, transition: "opacity .15s" }}>
    {/* One filter row above everything it scopes - never per-chart filters. */}
    <section className="voucher-card voucher-filter-row">
      <label>Department<select value={filters.department} onChange={e => setFilters({ ...filters, department: e.target.value })}>
        <option value="">All</option>
        {departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
      </select></label>
      <label>Voucher type<select value={filters.voucher_type} onChange={e => setFilters({ ...filters, voucher_type: e.target.value })}>
        <option value="">All</option>
        {voucherTypes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
      </select></label>
      <label>Status<select value={filters.status} onChange={e => setFilters({ ...filters, status: e.target.value })}>
        <option value="">All</option>
        {["generated", "issued", "redeemed", "cancelled"].map(s => <option key={s} value={s}>{s}</option>)}
      </select></label>
      <label>From<input type="date" value={filters.from} onChange={e => setFilters({ ...filters, from: e.target.value })} /></label>
      <label>To<input type="date" value={filters.to} onChange={e => setFilters({ ...filters, to: e.target.value })} /></label>
      <button type="button" className="link-button" onClick={() => setFilters({ department: "", voucher_type: "", status: "", from: "", to: "" })}>Clear</button>
    </section>

    <section className="voucher-stats">
      <div><span>Total</span><strong>{summary.total.toLocaleString()}</strong></div>
      <div><span>Generated</span><strong>{summary.generated.toLocaleString()}</strong></div>
      <div><span>Issued</span><strong>{summary.issued.toLocaleString()}</strong></div>
      <div><span>Redeemed</span><strong>{summary.redeemed.toLocaleString()}</strong></div>
      <div><span>Expired</span><strong>{summary.expired.toLocaleString()}</strong></div>
      <div><span>Cancelled</span><strong>{summary.cancelled.toLocaleString()}</strong></div>
    </section>

    <ChartOrTable
      title="Vouchers over time"
      chart={<TrendChart data={trend} />}
      table={<div className="table-wrap">
        <table>
          <thead><tr><th>Month</th><th>Created</th><th>Issued</th><th>Redeemed</th></tr></thead>
          <tbody>{trend.map(row => <tr key={row.month}>
            <td>{row.month}</td><td>{row.created}</td><td>{row.issued}</td><td>{row.redeemed}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    />

    <ChartOrTable
      title="By department"
      action={<button type="button" className="secondary" style={{ width: "auto", padding: "0 14px", height: 34 }}
        onClick={() => downloadBlob(`voucher-portal/reports/export/${query()}`, "voucher-report.csv")}>Download CSV</button>}
      chart={departmentSeries.length ? <DepartmentChart rows={departmentSeries} /> : <div className="data-state">Nothing in this slice.</div>}
      table={breakdownTable(byDepartment, "batch__department__id", "batch__department__name", "Department")}
    />

    <section className="voucher-card">
      <div className="voucher-card-head"><h2>By voucher type</h2></div>
      {breakdownTable(byType, "batch__voucher_type__id", "batch__voucher_type__name", "Voucher type")}
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head"><h2>Batches</h2></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Batch</th><th>Department</th><th>Type</th><th>Value</th><th>Status</th><th>Generated</th><th>Issued</th><th>Redeemed</th><th>Valid until</th></tr></thead>
          <tbody>
            {batchRows.map(row => <tr key={row.id}>
              <td><strong>{row.name}</strong><small>{row.prefix}</small></td>
              <td>{row.department}</td><td>{row.voucher_type}</td><td>{row.value}</td>
              <td><span className={"status " + statusClass(row.status)}>{statusLabel(row.status)}</span></td>
              <td>{row.generated}</td><td>{row.issued}</td><td>{row.redeemed}</td><td>{row.valid_to}</td>
            </tr>)}
            {batchRows.length === 0 && <tr><td colSpan={9}><div className="data-state">Nothing in this slice.</div></td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  </div>;
}

type GeometryField = { key: string; x: number; y: number; size?: number; w?: number; h?: number; line_height?: number; color?: string; font?: string; static?: string };
type Geometry = { artwork?: any; card?: any; fields: GeometryField[] };
type CatalogueEntry = { key: string; label: string; kind: string; has_static_text?: boolean };

/** Drag-to-position editor for a template's field layout (§5 "user-editable
 *  field positions"). Positions are points from the coupon's top-left corner,
 *  which is exactly what the PDF renderer consumes - the canvas below is that
 *  same coordinate space scaled to fit, so what you drag is what prints. */
function GeometryEditor({ template, onClose, onSaved }: { template: Template; onClose: () => void; onSaved: () => void }) {
  const COUPON_W = 479.52, COUPON_H = 178;
  const DISPLAY_W = 720;
  const scale = DISPLAY_W / COUPON_W;

  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    (async () => {
      const cat = await fmsRequest<{ fields: CatalogueEntry[]; defaults: Geometry }>("voucher-portal/templates/field-catalogue/");
      setCatalogue(cat.fields);
      const full = await fmsRequest<Template & { field_geometry: Geometry }>(`voucher-portal/templates/${template.id}/`);
      setGeometry(full.field_geometry?.fields ? full.field_geometry : cat.defaults);
    })();
  }, [template.id]);

  const labelFor = (key: string) => catalogue.find(c => c.key === key)?.label || key;
  const kindFor = (key: string) => catalogue.find(c => c.key === key)?.kind || "text";

  const patchField = (key: string, patch: Partial<GeometryField>) => {
    setGeometry(g => g && ({ ...g, fields: g.fields.map(f => f.key === key ? { ...f, ...patch } : f) }));
    setPreviewUrl("");
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (!dragging || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const x = Math.round(Math.max(0, Math.min(COUPON_W, (event.clientX - rect.left) / (rect.width / COUPON_W))) * 10) / 10;
    const y = Math.round(Math.max(0, Math.min(COUPON_H, (event.clientY - rect.top) / (rect.height / COUPON_H))) * 10) / 10;
    patchField(dragging, { x, y });
  };

  const save = async () => {
    if (!geometry) return;
    setSaving(true); setError("");
    try {
      await fmsRequest(`voucher-portal/templates/${template.id}/`, {
        method: "PATCH", body: JSON.stringify({ field_geometry: geometry }),
      });
      onSaved();
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setSaving(false); }
  };

  const resetToDefault = async () => {
    setSaving(true); setError("");
    try {
      const updated = await fmsRequest<{ field_geometry: Geometry }>(`voucher-portal/templates/${template.id}/reset-geometry/`, { method: "POST", body: "{}" });
      setGeometry(updated.field_geometry); setPreviewUrl("");
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setSaving(false); }
  };

  const renderPreview = async () => {
    if (!geometry) return;
    setPreviewing(true); setError("");
    try {
      const response = await fmsRequestRaw(`voucher-portal/templates/${template.id}/preview/`, {
        method: "POST", body: JSON.stringify({ field_geometry: geometry }),
      });
      setPreviewUrl(URL.createObjectURL(await response.blob()));
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setPreviewing(false); }
  };

  if (!geometry) return <section className="voucher-card"><div className="data-state">Loading layout…</div></section>;

  const active = geometry.fields.find(f => f.key === selected) || null;
  const card = geometry.card || {};

  return <section className="voucher-card">
    <div className="voucher-card-head">
      <h2>Layout — {template.name}</h2>
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button type="button" className="link-button" onClick={resetToDefault} disabled={saving}>Reset to default</button>
        <button type="button" className="link-button" onClick={renderPreview} disabled={previewing}>{previewing ? "Rendering…" : "Preview PDF"}</button>
        <button type="button" className="secondary" style={{ width: "auto", padding: "0 14px", height: 34 }} onClick={onClose}>Close</button>
        <button type="button" className="primary" style={{ width: "auto", padding: "0 16px", height: 34 }} disabled={saving} onClick={save}>{saving ? "Saving…" : "Save layout"}</button>
      </div>
    </div>

    <p style={{ fontSize: 11, color: "var(--voucher-muted)", marginBottom: 12 }}>
      Drag a field to move it, or pick one and type exact coordinates. Positions are
      in points from the coupon's top-left corner — the same units the printed PDF uses.
    </p>
    {error && <div className="form-error" style={{ marginBottom: 12 }}>{error}</div>}

    <div className="geo-layout">
      <div>
        <div ref={canvasRef} className="geo-canvas"
             style={{ width: DISPLAY_W, height: COUPON_H * scale, backgroundImage: template.artwork ? `url(${template.artwork})` : undefined }}
             onPointerMove={onPointerMove} onPointerUp={() => setDragging(null)} onPointerLeave={() => setDragging(null)}>
          {card.w && <div className="geo-card-box" style={{
            left: (card.x || 0) * scale, top: (card.y || 0) * scale,
            width: card.w * scale, height: (card.h || 0) * scale,
          }} />}
          {geometry.fields.map(field => (
            <button key={field.key} type="button"
                    className={"geo-chip" + (selected === field.key ? " selected" : "")}
                    style={{ left: field.x * scale, top: field.y * scale }}
                    onPointerDown={event => { event.preventDefault(); setSelected(field.key); setDragging(field.key); }}
                    onClick={() => setSelected(field.key)}>
              {labelFor(field.key)}
            </button>
          ))}
          {!template.artwork && <span className="geo-canvas-note">No artwork uploaded — the default ADCOOP design prints behind these fields.</span>}
        </div>
        {previewUrl && <iframe src={previewUrl} title="Template preview"
          style={{ width: DISPLAY_W, height: 320, marginTop: 14, border: "1px solid var(--voucher-line)", borderRadius: 10 }} />}
      </div>

      <div className="geo-panel">
        <p className="geo-panel-title">Fields</p>
        <ul className="geo-field-list">
          {geometry.fields.map(field => (
            <li key={field.key}>
              <button type="button" className={selected === field.key ? "selected" : ""} onClick={() => setSelected(field.key)}>
                {labelFor(field.key)}
              </button>
            </li>
          ))}
        </ul>

        {active && <div className="geo-props">
          <p className="geo-panel-title">{labelFor(active.key)}</p>
          <label>X (pt)<input type="number" step="0.1" min={0} max={COUPON_W} value={active.x}
            onChange={e => patchField(active.key, { x: Number(e.target.value) })} /></label>
          <label>Y (pt)<input type="number" step="0.1" min={0} max={COUPON_H} value={active.y}
            onChange={e => patchField(active.key, { y: Number(e.target.value) })} /></label>
          {kindFor(active.key) === "box" ? <>
            <label>Width (pt)<input type="number" step="0.1" min={0} value={active.w ?? 0}
              onChange={e => patchField(active.key, { w: Number(e.target.value) })} /></label>
            <label>Height (pt)<input type="number" step="0.1" min={0} value={active.h ?? 0}
              onChange={e => patchField(active.key, { h: Number(e.target.value) })} /></label>
          </> : <>
            <label>Font size (pt)<input type="number" step="0.5" min={0} value={active.size ?? 8}
              onChange={e => patchField(active.key, { size: Number(e.target.value) })} /></label>
            {kindFor(active.key) === "multiline" && <label>Line spacing (pt)<input type="number" step="0.5" min={0} value={active.line_height ?? 9}
              onChange={e => patchField(active.key, { line_height: Number(e.target.value) })} /></label>}
            <label>Colour<input type="color" value={active.color || "#231B36"}
              onChange={e => patchField(active.key, { color: e.target.value })} /></label>
          </>}
          {catalogue.find(c => c.key === active.key)?.has_static_text &&
            <label>Text<input value={active.static ?? ""} onChange={e => patchField(active.key, { static: e.target.value })} /></label>}
        </div>}
      </div>
    </div>
  </section>;
}

function TemplatesScreen({ canAdmin }: { canAdmin: boolean }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [editing, setEditing] = useState<Template | null>(null);

  const load = async () => {
    setLoading(true);
    const data = await fmsRequest<{ results: Template[] } | Template[]>("voucher-portal/templates/?page_size=100");
    setTemplates(unwrap(data)); setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const setDefault = async (template: Template) => {
    setBusyId(template.id);
    try { await fmsRequest(`voucher-portal/templates/${template.id}/`, { method: "PATCH", body: JSON.stringify({ is_default: true }) }); await load(); }
    finally { setBusyId(null); }
  };
  const toggleActive = async (template: Template) => {
    setBusyId(template.id);
    try { await fmsRequest(`voucher-portal/templates/${template.id}/`, { method: "PATCH", body: JSON.stringify({ is_active: !template.is_active }) }); await load(); }
    finally { setBusyId(null); }
  };

  const upload = async (file: File) => {
    setUploading(true); setUploadError("");
    try {
      const body = new FormData();
      body.append("name", uploadName || file.name);
      body.append("artwork", file);
      await fmsRequest("voucher-portal/templates/", { method: "POST", body });
      setUploadName(""); await load();
    } catch (error: any) { setUploadError(parseApiError(error)); }
    finally { setUploading(false); }
  };

  if (editing) {
    return <GeometryEditor template={editing} onClose={() => setEditing(null)}
                           onSaved={() => { setEditing(null); load(); }} />;
  }

  return <section className="voucher-card">
    <div className="voucher-card-head"><h2>Voucher templates</h2></div>
    {canAdmin && <div className="voucher-inline-form" style={{ marginBottom: 18 }}>
      <input placeholder="Template name" value={uploadName} onChange={e => setUploadName(e.target.value)} style={{ marginBottom: 8 }} />
      <input type="file" accept="image/png,image/jpeg" disabled={uploading}
        onChange={e => e.target.files?.[0] && upload(e.target.files[0])} />
      {uploading && <div className="data-state">Uploading…</div>}
      {uploadError && <div className="form-error">{uploadError}</div>}
    </div>}
    {loading && <div className="data-state">Loading…</div>}
    {!loading && <div className="voucher-template-grid">
      {templates.map(t => <div key={t.id} className="voucher-template-card">
        {t.artwork ? <img src={t.artwork} alt={t.name} /> : <div className="voucher-template-placeholder">Default ADCOOP design</div>}
        <strong>{t.name}</strong>
        <div className="voucher-template-badges">
          {t.is_default && <span className="chip active">Default</span>}
          {!t.is_active && <span className="chip">Inactive</span>}
        </div>
        {canAdmin && <div className="voucher-template-actions">
          {!t.is_default && <button type="button" className="link-button" disabled={busyId === t.id} onClick={() => setDefault(t)}>Set default</button>}
          <button type="button" className="link-button" disabled={busyId === t.id} onClick={() => toggleActive(t)}>{t.is_active ? "Deactivate" : "Activate"}</button>
          <button type="button" className="link-button" onClick={() => setEditing(t)}>Edit layout</button>
        </div>}
      </div>)}
      {templates.length === 0 && <div className="data-state">No templates yet.</div>}
    </div>}
  </section>;
}

function AccessScreen({ departments }: { departments: Department[] }) {
  const [grants, setGrants] = useState<AccessGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState({ username: "", role: "requester", departmentIds: [] as number[] });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    const data = await fmsRequest<{ results: AccessGrant[] } | AccessGrant[]>("voucher-portal/access/?page_size=200");
    setGrants(unwrap(data)); setLoading(false);
  };
  useEffect(() => { load(); }, []);

  const grantAccess = async () => {
    setSaving(true); setError("");
    try {
      await fmsRequest("voucher-portal/access/", {
        method: "POST",
        body: JSON.stringify({ user: form.username, role: form.role, department_ids: form.departmentIds }),
      });
      setForm({ username: "", role: "requester", departmentIds: [] });
      await load();
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setSaving(false); }
  };

  const toggleActive = async (grant: AccessGrant) => {
    await fmsRequest(`voucher-portal/access/${grant.id}/`, { method: "PATCH", body: JSON.stringify({ is_active: !grant.is_active }) });
    await load();
  };

  const toggleDept = (id: number) => {
    setForm(f => ({ ...f, departmentIds: f.departmentIds.includes(id) ? f.departmentIds.filter(d => d !== id) : [...f.departmentIds, id] }));
  };

  return <section className="voucher-card">
    <div className="voucher-card-head"><h2>Team access</h2></div>
    <div className="voucher-inline-form" style={{ marginBottom: 20 }}>
      <input placeholder="Existing username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} style={{ marginBottom: 8 }} />
      <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} style={{ marginBottom: 8 }}>
        <option value="requester">Requester</option>
        <option value="approver">Approver</option>
        <option value="report_viewer">Report Viewer</option>
        <option value="administrator">Administrator</option>
      </select>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
        {departments.map(d => <label key={d.id} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}>
          <input type="checkbox" checked={form.departmentIds.includes(d.id)} onChange={() => toggleDept(d.id)} />{d.name}
        </label>)}
      </div>
      {error && <div className="form-error" style={{ marginBottom: 8 }}>{error}</div>}
      <button type="button" className="primary" disabled={saving || !form.username.trim()} onClick={grantAccess}>Grant access</button>
    </div>

    {loading && <div className="data-state">Loading…</div>}
    {!loading && <div className="table-wrap">
      <table>
        <thead><tr><th>User</th><th>Role</th><th>Departments</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {grants.map(g => <tr key={g.id}>
            <td><strong>{g.username}</strong></td>
            <td>{ROLE_LABELS[g.role] || g.role}</td>
            <td>{g.department_names.length ? g.department_names.join(", ") : "All"}</td>
            <td><span className={"status " + (g.is_active ? "approved" : "cancelled")}>{g.is_active ? "active" : "inactive"}</span></td>
            <td><button type="button" className="link-button" onClick={() => toggleActive(g)}>{g.is_active ? "Deactivate" : "Reactivate"}</button></td>
          </tr>)}
          {grants.length === 0 && <tr><td colSpan={5}><div className="data-state">No grants yet.</div></td></tr>}
        </tbody>
      </table>
    </div>}
  </section>;
}
