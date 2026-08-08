"use client";

import { useEffect, useState } from "react";
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

async function downloadBlob(path: string, filename: string) {
  const response = await fmsRequestRaw(path);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
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
    return <main className="voucher-page">{nav}<ReportsScreen /></main>;
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
        {activeBatch.combined_pdf_url && <a className="secondary voucher-download-link"
          href={activeBatch.combined_pdf_url} target="_blank" rel="noreferrer">Download print-ready PDF (all vouchers)</a>}

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
                      {v.pdf_url && <a className="link-button" href={v.pdf_url} target="_blank" rel="noreferrer">PDF</a>}
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

function ReportsScreen() {
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const [byDepartment, setByDepartment] = useState<any[]>([]);
  const [byType, setByType] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const [s, d, t] = await Promise.all([
        fmsRequest<Record<string, number>>("voucher-portal/reports/summary/"),
        fmsRequest<any[]>("voucher-portal/reports/by-department/"),
        fmsRequest<any[]>("voucher-portal/reports/by-type/"),
      ]);
      setSummary(s); setByDepartment(d); setByType(t); setLoading(false);
    })();
  }, []);

  if (loading || !summary) return <div className="data-state">Loading…</div>;

  return <>
    <section className="voucher-stats">
      <div><span>Total</span><strong>{summary.total}</strong></div>
      <div><span>Generated</span><strong>{summary.generated}</strong></div>
      <div><span>Issued</span><strong>{summary.issued}</strong></div>
      <div><span>Redeemed</span><strong>{summary.redeemed}</strong></div>
      <div><span>Expired</span><strong>{summary.expired}</strong></div>
      <div><span>Cancelled</span><strong>{summary.cancelled}</strong></div>
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head">
        <h2>By department</h2>
        <button type="button" className="secondary" onClick={() => downloadBlob("voucher-portal/reports/export/", "voucher-report.csv")}>Download CSV</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Department</th><th>Total</th><th>Issued</th><th>Redeemed</th></tr></thead>
          <tbody>
            {byDepartment.map((row: any) => <tr key={row.batch__department__id}>
              <td>{row.batch__department__name}</td><td>{row.total}</td><td>{row.issued}</td><td>{row.redeemed}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head"><h2>By voucher type</h2></div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Voucher type</th><th>Total</th><th>Issued</th><th>Redeemed</th></tr></thead>
          <tbody>
            {byType.map((row: any) => <tr key={row.batch__voucher_type__id}>
              <td>{row.batch__voucher_type__name}</td><td>{row.total}</td><td>{row.issued}</td><td>{row.redeemed}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>
  </>;
}

function TemplatesScreen({ canAdmin }: { canAdmin: boolean }) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");

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
