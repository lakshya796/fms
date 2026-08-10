"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { fmsRequest, fmsRequestRaw, login, logout, UNAUTHORISED_EVENT } from "../lib/fms-api";

// Authenticated ADCOOP Voucher Portal. Unlike /vouchers (deliberately public),
// every call here goes through fmsRequest/fmsRequestRaw with the same
// fms_token session the rest of the console uses, and every screen is gated
// by the caller's role + department scope (see /access/me/).

type Department = { id: number; code: string; name: string };
type VoucherType = { id: number; code: string; name: string; department: number };
type Prefix = { id: number; prefix: string; label: string; department: number; voucher_type: number; sequence_length: number; next_sequence: number };
type Template = { id: number; name: string; artwork: string | null; artwork_path?: string | null;
  is_default: boolean; is_active: boolean;
  // Sent by every templates endpoint; optional so an older API can't crash the picker.
  layout?: CardDocument; field_geometry?: CardDocument; coupon_width?: number; coupon_height?: number };
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
type DesignIntent =
  | { mode: "edit"; template: Template; previewArtwork?: File | null }
  | { mode: "new"; previewArtwork?: File | null }
  | null;
type AccessGrant = { id: number; user: string; username: string; role: string; department_ids: number[]; department_names: string[]; is_active: boolean };

const EMPTY_FORM = {
  name: "", department: "", voucher_type: "", description: "", quantity: "10",
  discount_type: "fixed", percentage_value: "", max_discount_value: "", fixed_value: "", currency: "AED",
  valid_to: "", restrictions: "", terms: "", prefix: "", template: "",
};

/** Which placeholder prints each value the create-batch form collects.
 *
 *  Mirrored by `BatchFieldCoverageTests` on the server: a field collected from
 *  the requester with nowhere to print is a value that silently never reaches
 *  the voucher, so the form says which of the things you typed this card will
 *  actually show. */
const FORM_FIELD_PLACEHOLDERS: {
  label: string; field: keyof typeof EMPTY_FORM; sources: string[];
  /** Worth warning about when it's filled in and the card can't print it.
   *  Operational values (quantity, prefix, department) are placeable but
   *  rarely printed, so flagging them would be noise on every batch. */
  notify?: boolean;
}[] = [
  { label: "Voucher name", field: "name", sources: ["batch_name"] },
  { label: "Quantity", field: "quantity", sources: ["quantity"] },
  { label: "Department", field: "department", sources: ["department"] },
  { label: "Voucher type", field: "voucher_type", sources: ["voucher_type"] },
  { label: "Prefix", field: "prefix", sources: ["prefix"] },
  { label: "Currency", field: "currency", sources: ["currency", "discount_unit", "discount_value", "discount_cap"] },
  { label: "Description", field: "description", sources: ["description"], notify: true },
  { label: "Discount", field: "discount_type", sources: ["discount_value", "discount_numeral", "discount_type"], notify: true },
  { label: "Maximum discount", field: "max_discount_value", sources: ["discount_cap", "max_discount_value"], notify: true },
  { label: "Valid until", field: "valid_to", sources: ["valid_to"], notify: true },
  { label: "Restrictions", field: "restrictions", sources: ["restrictions"], notify: true },
  { label: "Terms and conditions", field: "terms", sources: ["terms"], notify: true },
];

/** The variables a design actually prints, from its visible field elements. */
function printedSources(document: CardDocument | null): Set<string> {
  return new Set((document?.elements || [])
    .filter(element => element.type === "field" && !element.hidden && element.source)
    .map(element => element.source as string));
}

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
  const [screen, setScreen] = useState<"batches" | "reports" | "templates" | "setup" | "access">("batches");

  const [departments, setDepartments] = useState<Department[]>([]);
  const [voucherTypes, setVoucherTypes] = useState<VoucherType[]>([]);
  const [prefixes, setPrefixes] = useState<Prefix[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  // Set when the create-batch form sends the user off to design a card; it
  // brings them back to the half-filled form afterwards.
  const [designIntent, setDesignIntent] = useState<DesignIntent>(null);
  const [sampleValues, setSampleValues] = useState<Record<string, string>>({});
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
  const [batchArtwork, setBatchArtwork] = useState<File | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

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
    await loadTemplates();
    // Sample values come from the server so a picker thumbnail shows the same
    // stand-ins as the designer canvas and the PDF proof.
    try {
      const catalogue = await fmsRequest<Catalogue>("voucher-portal/templates/field-catalogue/");
      if (usableCatalogue(catalogue)) {
        setSampleValues(Object.fromEntries(catalogue.variables.map(v => [v.key, v.sample])));
      }
    } catch { /* an older API has no catalogue; thumbnails just show fewer values */ }
  };

  const loadTemplates = async () => {
    const data = await fmsRequest<{ results: Template[] } | Template[]>(
      "voucher-portal/templates/?page_size=100");
    const list = unwrap(data).filter(template => template.is_active);
    setTemplates(list);
    return list;
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
    // Keeps the picked card design: it's a choice about the batch, not a value
    // typed into it, and re-picking it on every new batch is busywork.
    setForm({ ...EMPTY_FORM, template: form.template });
    setBatchArtwork(null);
    setPreviewHash(""); setPreviewUrl(""); setPreviewError(""); setCreateError("");
  };

  // Pick the default design up front so the form is usable without touching
  // the picker at all - it stays the "just make me a batch" path.
  useEffect(() => {
    if (!templates.length || form.template) return;
    const fallback = templates.find(t => t.is_default) || templates[0];
    if (fallback) setForm(f => ({ ...f, template: String(fallback.id) }));
  }, [templates, form.template]);

  const selectedTemplate = templates.find(t => String(t.id) === form.template) || null;
  // Which of the values being typed the chosen card will actually show. Only
  // fields with something in them are worth flagging - an empty Terms box
  // isn't a problem, a filled one with nowhere to print is.
  const printed = printedSources(cardDocument(selectedTemplate));
  const filledFields = FORM_FIELD_PLACEHOLDERS.filter(entry => {
    if (entry.field === "max_discount_value") return form.discount_type === "percentage" && !!form.max_discount_value;
    if (entry.field === "discount_type") return !!(form.percentage_value || form.fixed_value);
    return !!String(form[entry.field] || "").trim();
  });
  const missingFields = filledFields.filter(
    entry => entry.notify && !entry.sources.some(source => printed.has(source)));
  const printedFields = FORM_FIELD_PLACEHOLDERS.filter(
    entry => entry.sources.some(source => printed.has(source)));
  const departmentPrefixes = prefixes.filter(p => !form.department || String(p.department) === form.department);
  const departmentTypes = voucherTypes.filter(t => !form.department || String(t.department) === form.department);
  const visibleDepartments = access?.department_ids
    ? departments.filter(d => access.department_ids!.includes(d.id))
    : departments;

  const updateForm = (patch: Partial<typeof form>) => {
    setForm(f => ({ ...f, ...patch }));
    setPreviewHash(""); setPreviewUrl(""); // any change invalidates the preview
  };

  const buildPayload = () => ({
    name: form.name, department: form.department, voucher_type: form.voucher_type, description: form.description,
    quantity: form.quantity, discount_type: form.discount_type,
    percentage_value: form.discount_type === "percentage" ? form.percentage_value : undefined,
    max_discount_value: form.discount_type === "percentage" ? (form.max_discount_value || undefined) : undefined,
    fixed_value: form.discount_type === "fixed" ? form.fixed_value : undefined,
    currency: form.currency, valid_to: form.valid_to,
    restrictions: form.restrictions, terms: form.terms, prefix: form.prefix,
    template: form.template || undefined,
  });

  const buildFormData = (includePreviewHash = false) => {
    const body = new FormData();
    Object.entries(buildPayload()).forEach(([key, value]) => {
      if (value !== undefined && value !== null) body.append(key, String(value));
    });
    if (batchArtwork) body.append("artwork", batchArtwork);
    if (includePreviewHash) body.append("preview_hash", previewHash);
    return body;
  };

  const runPreview = async () => {
    setPreviewing(true); setPreviewError(""); setPreviewUrl("");
    try {
      const response = await fmsRequestRaw("voucher-portal/batches/preview/", { method: "POST", body: buildFormData() });
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
      await fmsRequest("voucher-portal/batches/", { method: "POST", body: buildFormData(true) });
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
      {(access.actions.includes("admin") || access.actions.includes("create")) && <button type="button" className={"link-button" + (screen === "templates" ? " active" : "")} onClick={() => { setDesignIntent(null); setScreen("templates"); }}>Templates</button>}
      {access.actions.includes("admin") && <button type="button" className={"link-button" + (screen === "setup" ? " active" : "")} onClick={() => { setDesignIntent(null); setScreen("setup"); }}>Setup</button>}
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
      {/* Hidden while a design shortcut has taken over the screen: it belongs to
          the batch form, and pressing it there would quietly discard the draft
          the user is coming back to. */}
      {screen === "batches" && !activeBatch && !designIntent && access.actions.includes("create") &&
        <button className="primary" onClick={() => { setShowCreate(v => !v); resetCreateForm(); }}>{showCreate ? "Close" : "New batch"}</button>}
      <button className="secondary" onClick={onSignOut}>Sign out</button>
    </div>
  </header>;

  if (screen === "reports") {
    return <main className="voucher-page">{nav}<ReportsScreen departments={departments} voucherTypes={voucherTypes} /></main>;
  }
  if (screen === "templates" || designIntent) {
    return <main className="voucher-page">{nav}
      <TemplatesScreen canAdmin={access.actions.includes("admin")} intent={designIntent}
        onIntentDone={async designed => {
          setDesignIntent(null);
          const list = await loadTemplates();
          // Come back to the form the shortcut was launched from, with the
          // design they just worked on selected and the preview invalidated -
          // it was rendered against the old design.
          if (designed && list.some(t => t.id === designed)) updateForm({ template: String(designed) });
          else { setPreviewHash(""); setPreviewUrl(""); }
        }} />
    </main>;
  }
  if (screen === "setup") {
    return <main className="voucher-page">{nav}
      <SetupScreen onChanged={loadReferenceData} />
    </main>;
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

        <label className="voucher-form-wide">Voucher image <small>(optional — overrides the selected design artwork for this batch)</small>
          <input type="file" accept="image/png,image/jpeg" onChange={e => {
            setBatchArtwork(e.target.files?.[0] || null);
            setPreviewHash(""); setPreviewUrl(""); setPreviewError("");
          }} />
          {batchArtwork && <small>{batchArtwork.name} · used in both the preview and generated PDFs.</small>}
        </label>

        <div className="voucher-form-wide voucher-picker">
          <div className="voucher-picker-head">
            <span>Card design</span>
            <div className="voucher-picker-actions">
              {selectedTemplate && <button type="button" className="link-button"
                onClick={() => setDesignIntent({ mode: "edit", template: selectedTemplate, previewArtwork: batchArtwork })}>
                Edit this design
              </button>}
              <button type="button" className="link-button" onClick={() => setDesignIntent({ mode: "new", previewArtwork: batchArtwork })}>
                New design
              </button>
            </div>
          </div>
          <div className="voucher-picker-row">
            {templates.map(template => (
              <button key={template.id} type="button"
                      className={"voucher-picker-card" + (String(template.id) === form.template ? " selected" : "")}
                      onClick={() => updateForm({ template: String(template.id) })}>
                <CardThumbnail template={template} document={cardDocument(template)}
                               width={template.coupon_width || 479.52} height={template.coupon_height || 178}
                               display={150} values={sampleValues} />
                <span>{template.name}</span>
                {template.is_default && <small>Default</small>}
              </button>
            ))}
            {templates.length === 0 && <div className="data-state">
              No card designs yet — “New design” creates one.
            </div>}
          </div>
          {selectedTemplate && <div className="voucher-picker-coverage">
            <p className="ok">
              This card prints: <strong>{["Voucher number (barcode)", ...printedFields.map(entry => entry.label)].join(", ")}</strong>.
            </p>
            {missingFields.length > 0 && <p className="warn">
                  Not on this card: <strong>{missingFields.map(entry => entry.label).join(", ")}</strong>.
                  {" "}You've entered {missingFields.length === 1 ? "it" : "them"}, but the design has no field to
                  print {missingFields.length === 1 ? "it" : "them"} in — add {missingFields.length === 1 ? "one" : "them"} with
                  {" "}<button type="button" className="link-button"
                     onClick={() => setDesignIntent({ mode: "edit", template: selectedTemplate, previewArtwork: batchArtwork })}>Edit this design</button>.
              </p>}
          </div>}
          <p className="voucher-picker-help">
            Need another value on the voucher? Choose <strong>Edit this design</strong>, then use <strong>+ Form field</strong>
            to place any batch-form placeholder on the card.
          </p>
        </div>

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

// ---------------------------------------------------------------------------
// Template designer
//
// A card starts empty apart from the one mandatory barcode; every other thing
// on it - headline, validity line, terms, panels, rules - is an element the
// user adds and positions themselves. The canvas is the card's own coordinate
// space (points from the top-left corner) scaled to fit, drawn with the same
// values the server's sample PDF uses, so what you drag really is what prints.
// ---------------------------------------------------------------------------

type ElementType = "text" | "field" | "box" | "line" | "barcode";
type Align = "left" | "center" | "right";

type CardElement = {
  id: string; type: ElementType; name?: string;
  x: number; y: number; w?: number; h?: number; hidden?: boolean;
  text?: string; source?: string; prefix?: string; suffix?: string;
  size?: number; font?: string; color?: string; align?: Align;
  line_height?: number; wrap?: number; max_lines?: number;
  fill?: string; opacity?: number; radius?: number; border_color?: string; border_width?: number;
  show_value?: boolean; value_size?: number; value_font?: string; value_color?: string;
  hide_if_empty?: string;
};

type CardDocument = {
  version: number;
  coupon?: { w: number; h: number };
  background?: string;
  artwork?: { x: number; y: number; w: number; h: number; hidden?: boolean };
  elements: CardElement[];
};

type Variable = { key: string; label: string; sample: string; multiline?: boolean };
type PaletteEntry = { type: ElementType; label: string; hint: string; defaults: Partial<CardElement> };
type CouponPreset = { key: string; label: string; w: number; h: number };
type Starter = { key: string; label: string; description: string; geometry: CardDocument };
type Catalogue = {
  variables: Variable[]; palette: PaletteEntry[]; element_types: ElementType[];
  fonts: string[]; alignments: Align[]; coupon_presets: CouponPreset[];
  starters: Starter[]; blank: CardDocument;
};
type TemplateSummary = Template;
type TemplateDetail = Template;

const API_TOO_OLD = "This screen needs a newer version of the voucher portal API than the server is running. " +
  "Ask an administrator to deploy the latest backend, then reload.";

/** The layout to draw for a template.
 *
 *  The frontend and the API deploy separately, so a browser can be a release
 *  ahead of the server. An API from before the card designer sends no
 *  `layout`, and its `field_geometry` is the old fixed-field document - which
 *  is readable but not drawable here. Return null for both rather than
 *  handing the renderer something it will crash on. */
function cardDocument(template: TemplateSummary | null | undefined): CardDocument | null {
  const candidate = template?.layout || template?.field_geometry;
  return candidate && candidate.version === 3 && Array.isArray(candidate.elements) ? candidate : null;
}

/** Does this API know about the designer at all? */
function usableCatalogue(catalogue: Catalogue | null): catalogue is Catalogue {
  return !!catalogue && Array.isArray(catalogue.palette) && Array.isArray(catalogue.variables)
    && Array.isArray(catalogue.coupon_presets);
}

const TYPE_LABELS: Record<ElementType, string> = {
  text: "Text", field: "Voucher field", box: "Box", line: "Line", barcode: "Barcode",
};

const TYPE_GLYPHS: Record<ElementType, string> = {
  text: "T", field: "\u0192", box: "\u25AD", line: "\u2014", barcode: "\u2016",
};

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"] as const;
type Handle = typeof HANDLES[number];

/** reportlab's built-in faces mapped onto something a browser has. */
function fontStyle(font = "Helvetica"): React.CSSProperties {
  return {
    fontFamily: font.startsWith("Times") ? '"Times New Roman", Times, serif'
      : font.startsWith("Courier") ? '"Courier New", Courier, monospace'
      : "Helvetica, Arial, sans-serif",
    fontWeight: font.includes("Bold") ? 700 : 400,
    fontStyle: font.includes("Oblique") || font.includes("Italic") ? "italic" : "normal",
  };
}

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const round = (value: number) => Math.round(value * 10) / 10;

function defaultName(element: CardElement, variables: Variable[]) {
  if (element.name) return element.name;
  if (element.type === "text") return (element.text || "Text").split("\n")[0].slice(0, 28) || "Text";
  if (element.type === "field") return variables.find(v => v.key === element.source)?.label || "Voucher field";
  return TYPE_LABELS[element.type];
}

/** What this element prints, given a set of sample or real values. */
function elementText(element: CardElement, values: Record<string, string>) {
  if (element.type === "text") return element.text || "";
  const value = values[element.source || ""] ?? "";
  if (!String(value).trim()) return "";
  return `${element.prefix || ""}${value}${element.suffix || ""}`;
}

/** Bar widths that look like a barcode and stay put between renders, so a
 *  preview doesn't shimmer while you drag it. The scannable article is in the
 *  PDF proof - this is a placement guide. */
function barcodePattern(value: string, color: string) {
  let seed = 7;
  for (const character of value || "0") seed = (seed * 31 + character.charCodeAt(0)) % 9973;
  const stops: string[] = [];
  let position = 0;
  while (position < 100) {
    seed = (seed * 1103515245 + 12345) % 2147483647;
    const bar = 0.5 + (seed % 5) * 0.42;
    const gap = 0.5 + ((seed >> 5) % 4) * 0.36;
    const end = Math.min(position + bar, 100);
    stops.push(`${color} ${position}%`, `${color} ${end}%`,
               `transparent ${end}%`, `transparent ${Math.min(end + gap, 100)}%`);
    position = end + gap;
  }
  return `linear-gradient(90deg, ${stops.join(",")})`;
}

function ElementView({ element, scale, values }: { element: CardElement; scale: number; values: Record<string, string> }) {
  const left = element.x * scale;
  const top = element.y * scale;

  if (element.type === "box") {
    return <div className="card-el-box" style={{
      left, top, width: (element.w || 0) * scale, height: (element.h || 0) * scale,
      background: element.fill || "#FFFFFF", opacity: element.opacity ?? 1,
      borderRadius: (element.radius || 0) * scale,
      border: element.border_width ? `${Math.max(element.border_width * scale, 1)}px solid ${element.border_color || "#DCD7E8"}` : undefined,
    }} />;
  }

  if (element.type === "line") {
    return <div className="card-el-box" style={{
      left, top, width: (element.w || 0) * scale, height: Math.max((element.h || 1) * scale, 1),
      background: element.color || "#DCD7E8",
    }} />;
  }

  if (element.type === "barcode") {
    const value = values.voucher_code || "";
    const captionSize = (element.value_size || 7) * scale;
    return <div className="card-el-box" style={{ left, top, width: (element.w || 0) * scale }}>
      <div style={{ height: (element.h || 0) * scale, backgroundImage: barcodePattern(value, element.color || "#000000") }} />
      {element.show_value !== false && <div style={{
        ...fontStyle(element.value_font || "Courier"), fontSize: captionSize, lineHeight: `${captionSize * 1.3}px`,
        color: element.value_color || "#231B36", textAlign: "center", marginTop: scale,
      }}>{value}</div>}
    </div>;
  }

  // text and field share every typographic control
  const text = elementText(element, values);
  if (!text) return null;
  const size = (element.size || 8) * scale;
  const lineHeight = (element.line_height || (element.size || 8) + 2) * scale;
  const width = (element.w || 0) * scale;
  const align = element.align || "left";
  let lines = text.split("\n");
  if (!width && element.wrap) {
    lines = lines.flatMap(line => line.match(new RegExp(`.{1,${element.wrap}}(\\s|$)`, "g")) || [line]);
  }
  if (element.max_lines) lines = lines.slice(0, element.max_lines);

  return <div className="card-el-text" style={{
    left, // the PDF puts the glyph's cap-top on y; a CSS line box centres it,
          // so lift the block by the half-leading to line the two up.
    top: top - (lineHeight - size * 1.117) / 2 - size * 0.128,
    width: width || undefined,
    transform: width ? undefined : align === "center" ? "translateX(-50%)" : align === "right" ? "translateX(-100%)" : undefined,
    textAlign: align, color: element.color || "#231B36",
    fontSize: size, lineHeight: `${lineHeight}px`,
    whiteSpace: width ? "pre-wrap" : "pre",
    maxHeight: element.max_lines ? lineHeight * element.max_lines : undefined,
    overflow: element.max_lines ? "hidden" : undefined,
    ...fontStyle(element.font),
  }}>{lines.join("\n")}</div>;
}

/** Loads a template's artwork through the authenticated API.
 *
 *  The stored media URL is a 404 in production - nothing serves /media/ there
 *  - and an <img src> can't carry the session token anyway, so the bytes are
 *  fetched the same way voucher PDFs are and handed to the tag as an object
 *  URL. */
function useArtwork(path: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!path) { setUrl(null); return; }
    let cancelled = false;
    let objectUrl = "";
    (async () => {
      try {
        const response = await fmsRequestRaw(path);
        objectUrl = URL.createObjectURL(await response.blob());
        if (cancelled) URL.revokeObjectURL(objectUrl);
        else setUrl(objectUrl);
      } catch { if (!cancelled) setUrl(null); }
    })();
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path]);
  return url;
}

/** Creates a browser-only preview URL for batch artwork. The file is never
 * uploaded by the template editor, so saving a shared design cannot replace
 * its permanent artwork with a one-off batch image. */
function useFileArtwork(file: File | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file) { setUrl(null); return; }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return url;
}

/** The card itself: artwork, background and every element, drawn to scale.
 *  Shared by the designer canvas and the small previews on the library screen,
 *  so a thumbnail can never disagree with the editor about a layout. */
function CardPreview({ document: doc, artwork, width, height, scale, values, children }: {
  document: CardDocument | null; artwork: string | null; width: number; height: number;
  scale: number; values: Record<string, string>; children?: React.ReactNode;
}) {
  return <div className="card-surface" style={{
    width: width * scale, height: height * scale, background: doc?.background || "#FFFFFF",
  }}>
    {artwork && !doc?.artwork?.hidden && <img src={artwork} alt="" className="card-artwork" style={{
      left: (doc?.artwork?.x ?? 0) * scale, top: (doc?.artwork?.y ?? 0) * scale,
      width: (doc?.artwork?.w ?? width) * scale, height: (doc?.artwork?.h ?? height) * scale,
    }} />}
    {(doc?.elements || []).filter(element => !element.hidden).map(element => (
      <ElementView key={element.id} element={element} scale={scale} values={values} />
    ))}
    {children}
  </div>;
}

function TemplateDesigner({ template, canAdmin, previewArtwork, onClose, onSaved }: {
  template: Template; canAdmin: boolean; previewArtwork?: File | null;
  onClose: () => void; onSaved: () => void;
}) {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [doc, setDoc] = useState<CardDocument | null>(null);
  const [saved, setSaved] = useState("");
  const [card, setCard] = useState({ w: 479.52, h: 178 });
  const [artworkPath, setArtworkPath] = useState<string | null | undefined>(template.artwork_path);
  const storedArtwork = useArtwork(artworkPath);
  const batchArtworkPreview = useFileArtwork(previewArtwork);
  const artwork = batchArtworkPreview || storedArtwork;

  const [selected, setSelected] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [showProof, setShowProof] = useState(true);
  const [proofUrl, setProofUrl] = useState("");
  const [proofing, setProofing] = useState(false);
  const [error, setError] = useState("");
  const [warning, setWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState("");
  const [past, setPast] = useState<CardDocument[]>([]);
  const [future, setFuture] = useState<CardDocument[]>([]);
  const [addingField, setAddingField] = useState(false);

  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ id: string; mode: "move" | Handle; x: number; y: number; from: CardElement } | null>(null);
  const coalesce = useRef<{ key: string; at: number } | null>(null);
  const proofSequence = useRef(0);

  useEffect(() => {
    (async () => {
      try {
        const [cat, full] = await Promise.all([
          fmsRequest<Catalogue>("voucher-portal/templates/field-catalogue/"),
          fmsRequest<TemplateDetail>(`voucher-portal/templates/${template.id}/`),
        ]);
        // `layout` is the stored document normalised to the current element
        // shape, so a template authored before the designer existed opens as
        // ordinary, editable elements rather than failing to load. An API too
        // old to send it at all is reported, not crashed on.
        const layout = cardDocument(full);
        if (!usableCatalogue(cat) || !layout) {
          setError(API_TOO_OLD);
          return;
        }
        setCatalogue(cat);
        setArtworkPath(full.artwork_path);
        setCard({ w: full.coupon_width || 479.52, h: full.coupon_height || 178 });
        setDoc(layout);
        setSaved(JSON.stringify(layout));
      } catch (err: any) { setError(parseApiError(err)); }
    })();
  }, [template.id]);

  // Fit the card to the available column on first load; the zoom control takes
  // over from there.
  const displayScale = (720 / card.w) * zoom;
  const values = useMemo(() => {
    const map: Record<string, string> = {};
    (catalogue?.variables || []).forEach(variable => { map[variable.key] = variable.sample; });
    return map;
  }, [catalogue]);

  const dirty = !!doc && JSON.stringify(doc) !== saved;
  const elements = doc?.elements || [];
  const active = elements.find(element => element.id === selected) || null;
  const barcodeCount = elements.filter(element => element.type === "barcode").length;

  /** One undo step is pushed when a drag starts, not on every pointer move. */
  const pushHistory = () => {
    if (doc) setPast(stack => [...stack.slice(-59), doc]);
    setFuture([]);
    coalesce.current = null;
  };

  /** `key` groups a burst of edits to the same control - typing in a text box
   *  is one undo step, not one per keystroke. */
  const edit = (next: CardDocument, key = "") => {
    const now = Date.now();
    const repeat = !!key && coalesce.current?.key === key && now - coalesce.current.at < 900;
    if (!repeat && doc) {
      setPast(stack => [...stack.slice(-59), doc]);
      setFuture([]);
    }
    coalesce.current = key ? { key, at: now } : null;
    setDoc(next);
    setWarning("");
  };

  /** Mid-drag updates: history was pushed once at pointer-down. */
  const patch = (id: string, changes: Partial<CardElement>) => {
    setDoc(current => current && ({
      ...current, elements: current.elements.map(el => el.id === id ? { ...el, ...changes } : el),
    }));
  };

  /** Property-panel edits, which do belong in the undo history. */
  const setProp = (id: string, changes: Partial<CardElement>, key: string) => {
    if (!doc) return;
    edit({ ...doc, elements: doc.elements.map(el => el.id === id ? { ...el, ...changes } : el) }, key);
  };

  const undo = () => {
    if (!past.length || !doc) return;
    setFuture(stack => [doc, ...stack].slice(0, 60));
    setDoc(past[past.length - 1]);
    setPast(stack => stack.slice(0, -1));
    coalesce.current = null;
  };

  const redo = () => {
    if (!future.length || !doc) return;
    setPast(stack => [...stack, doc]);
    setDoc(future[0]);
    setFuture(stack => stack.slice(1));
    coalesce.current = null;
  };

  const addElement = (type: ElementType, source?: string) => {
    if (!doc || !catalogue) return;
    const entry = catalogue.palette.find(item => item.type === type);
    const id = `${type}-${Date.now().toString(36)}`;
    const element: CardElement = {
      id, type, name: undefined,
      // Cascade down the card so a run of additions doesn't pile up in one spot.
      x: round(clamp(card.w * 0.08 + (elements.length % 4) * 8, 0, card.w - 20)),
      y: round(clamp(card.h * 0.1 + elements.length * 16, 0, card.h - 20)),
      ...(entry?.defaults as Partial<CardElement>),
      ...(source ? { source } : {}),
    };
    if (element.type === "field" && source) {
      const variable = catalogue.variables.find(item => item.key === source);
      element.name = variable?.label;
      if (variable?.multiline) { element.w = round(card.w * 0.3); element.max_lines = 5; }
    }
    edit({ ...doc, elements: [...doc.elements, element] });
    setSelected(id);
    setAddingField(false);
  };

  const duplicate = (id: string) => {
    if (!doc) return;
    const source = doc.elements.find(element => element.id === id);
    if (!source) return;
    const copy = { ...source, id: `${source.type}-${Date.now().toString(36)}`,
                   x: round(clamp(source.x + 6, 0, card.w)), y: round(clamp(source.y + 6, 0, card.h)) };
    edit({ ...doc, elements: [...doc.elements, copy] });
    setSelected(copy.id);
  };

  const remove = (id: string) => {
    if (!doc) return;
    const element = doc.elements.find(item => item.id === id);
    if (element?.type === "barcode" && barcodeCount < 2) {
      setWarning("The barcode is mandatory - it carries each voucher's unique number. Move or resize it instead.");
      return;
    }
    edit({ ...doc, elements: doc.elements.filter(item => item.id !== id) });
    setSelected(null);
  };

  const reorder = (id: string, direction: -1 | 1) => {
    if (!doc) return;
    const index = doc.elements.findIndex(element => element.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= doc.elements.length) return;
    const next = [...doc.elements];
    [next[index], next[target]] = [next[target], next[index]];
    edit({ ...doc, elements: next });
  };

  const alignTo = (edge: "left" | "hcenter" | "right" | "top" | "vcenter" | "bottom") => {
    if (!doc || !active) return;
    const width = active.w || 0;
    const height = active.type === "barcode" || active.type === "box" || active.type === "line"
      ? (active.h || 0) : (active.line_height || active.size || 8);
    const map: Record<string, Partial<CardElement>> = {
      left: { x: 0 }, right: { x: round(card.w - width) }, hcenter: { x: round((card.w - width) / 2) },
      top: { y: 0 }, bottom: { y: round(card.h - height) }, vcenter: { y: round((card.h - height) / 2) },
    };
    edit({ ...doc, elements: doc.elements.map(el => el.id === active.id ? { ...el, ...map[edge] } : el) });
  };

  const startDrag = (event: React.PointerEvent, id: string, mode: "move" | Handle) => {
    event.preventDefault();
    event.stopPropagation();
    const element = elements.find(item => item.id === id);
    if (!element) return;
    setSelected(id);
    pushHistory();
    dragRef.current = { id, mode, x: event.clientX, y: event.clientY, from: { ...element } };
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const perPoint = rect.width / card.w;
    const snap = (value: number) => event.altKey ? round(value) : Math.round(value * 2) / 2;
    let dx = (event.clientX - drag.x) / perPoint;
    let dy = (event.clientY - drag.y) / perPoint;
    if (event.shiftKey && drag.mode === "move") {
      if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0;
    }
    const from = drag.from;
    const sized = from.type === "box" || from.type === "line" || from.type === "barcode" || !!from.w;
    const minW = from.type === "barcode" ? 40 : 4;
    const minH = from.type === "barcode" ? 8 : 1;

    if (drag.mode === "move") {
      patch(drag.id, {
        x: snap(clamp(from.x + dx, 0, card.w - (from.w || 0))),
        y: snap(clamp(from.y + dy, 0, card.h - (from.h || 0))),
      });
      return;
    }
    if (!sized) return;

    const next: Partial<CardElement> = {};
    const width = from.w || 0;
    const height = from.h || 0;
    if (drag.mode.includes("e")) next.w = snap(clamp(width + dx, minW, card.w - from.x));
    if (drag.mode.includes("s")) next.h = snap(clamp(height + dy, minH, card.h - from.y));
    if (drag.mode.includes("w")) {
      const x = snap(clamp(from.x + dx, 0, from.x + width - minW));
      next.x = x; next.w = snap(width + (from.x - x));
    }
    if (drag.mode.includes("n")) {
      const y = snap(clamp(from.y + dy, 0, from.y + height - minH));
      next.y = y; next.h = snap(height + (from.y - y));
    }
    if (from.type === "text" || from.type === "field") delete next.h;
    patch(drag.id, next);
  };

  const endDrag = (event: React.PointerEvent) => {
    if (dragRef.current) (event.target as HTMLElement).releasePointerCapture?.(event.pointerId);
    dragRef.current = null;
  };

  // Keyboard: nudge, duplicate, delete, undo/redo. Ignored while a form control
  // has focus so typing a label doesn't move the element behind it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        event.shiftKey ? redo() : undo();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") { event.preventDefault(); redo(); return; }
      if (!selected || !doc) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "d") { event.preventDefault(); duplicate(selected); return; }
      if (event.key === "Escape") { setSelected(null); return; }
      if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); remove(selected); return; }
      const step = event.shiftKey ? 10 : 1;
      const moves: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step],
      };
      const move = moves[event.key];
      if (!move) return;
      event.preventDefault();
      const element = doc.elements.find(item => item.id === selected);
      if (!element) return;
      edit({ ...doc, elements: doc.elements.map(item => item.id === selected ? {
        ...item,
        x: round(clamp(item.x + move[0], 0, card.w - (item.w || 0))),
        y: round(clamp(item.y + move[1], 0, card.h - (item.h || 0))),
      } : item) }, `nudge:${selected}`);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // The PDF proof is the source of truth for what prints. The canvas above it
  // is instant; this catches up once you stop moving, and surfaces the same
  // validation the save will run.
  useEffect(() => {
    if (!doc || !showProof) return;
    const sequence = ++proofSequence.current;
    const timer = window.setTimeout(async () => {
      setProofing(true);
      try {
        const response = await fmsRequestRaw(`voucher-portal/templates/${template.id}/preview/`, {
          method: "POST", body: JSON.stringify({ field_geometry: doc }),
        });
        const url = URL.createObjectURL(await response.blob());
        if (sequence === proofSequence.current) {
          setProofUrl(previous => { if (previous) URL.revokeObjectURL(previous); return url; });
          setWarning("");
        } else URL.revokeObjectURL(url);
      } catch (err: any) {
        if (sequence === proofSequence.current) setWarning(parseApiError(err));
      } finally {
        if (sequence === proofSequence.current) setProofing(false);
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [doc, showProof, template.id]);

  /** Ignores half-typed sizes: "4" on the way to "479" would otherwise clamp
   *  every element onto a 4pt card, and no undo would bring the layout back. */
  const applyCardSize = (width: number, height: number) => {
    if (!doc || width < 72 || height < 72 || width > 2400 || height > 2400) return;
    // Shrinking the card would otherwise push elements off the edge, which the
    // server rejects on save - pull them back in instead.
    edit({
      ...doc,
      coupon: { w: width, h: height },
      artwork: { ...(doc.artwork || { x: 0, y: 0 }), x: 0, y: 0, w: width, h: height, hidden: doc.artwork?.hidden },
      elements: doc.elements.map(element => ({
        ...element,
        w: element.w ? Math.min(element.w, width) : element.w,
        h: element.h ? Math.min(element.h, height) : element.h,
        x: round(clamp(element.x, 0, Math.max(0, width - (element.w || 0)))),
        y: round(clamp(element.y, 0, Math.max(0, height - (element.h || 0)))),
      })),
    }, "card-size");
    setCard({ w: width, h: height });
  };

  const uploadArtwork = async (file: File) => {
    setBusy("artwork"); setError("");
    try {
      const body = new FormData();
      body.append("artwork", file);
      const updated = await fmsRequest<TemplateDetail>(`voucher-portal/templates/${template.id}/`, { method: "PATCH", body });
      setArtworkPath(updated.artwork_path);
      setProofUrl("");
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setBusy(""); }
  };

  const save = async () => {
    if (!doc) return;
    setSaving(true); setError("");
    try {
      await fmsRequest(`voucher-portal/templates/${template.id}/`, {
        method: "PATCH",
        body: JSON.stringify({ field_geometry: doc, coupon_width: card.w, coupon_height: card.h }),
      });
      setSaved(JSON.stringify(doc));
      onSaved();
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setSaving(false); }
  };

  const applyStarter = (starter: Starter) => {
    if (!doc) return;
    if (doc.elements.length > 1 && !window.confirm(`Replace the current design with "${starter.label}"?`)) return;
    edit({ ...starter.geometry, coupon: { w: card.w, h: card.h } });
    setSelected(null);
  };

  const clearCard = () => {
    if (!doc || !catalogue) return;
    if (!window.confirm("Remove everything except the barcode?")) return;
    const barcode = doc.elements.find(element => element.type === "barcode") || catalogue.blank.elements[0];
    edit({ ...doc, elements: [barcode] });
    setSelected(null);
  };

  const close = () => {
    if (dirty && !window.confirm("You have unsaved design changes. Close anyway?")) return;
    onClose();
  };

  if (error && !doc) return <section className="voucher-card"><div className="form-error">{error}</div>
    <button type="button" className="secondary" style={{ width: 120, marginTop: 12 }} onClick={onClose}>Back</button></section>;
  if (!doc || !catalogue) return <section className="voucher-card"><div className="data-state">Opening the designer…</div></section>;

  const sized = active && (active.type === "box" || active.type === "line" || active.type === "barcode");
  const typographic = active && (active.type === "text" || active.type === "field");

  return <section className="voucher-card designer">
    <div className="voucher-card-head designer-head">
      <div>
        <h2>{template.name}</h2>
        <p className="designer-sub">
          {dirty ? "Unsaved changes" : "All changes saved"} · {elements.length} element{elements.length === 1 ? "" : "s"} ·
          {" "}{Math.round(card.w)} × {Math.round(card.h)} pt
        </p>
      </div>
      <div className="designer-actions">
        <button type="button" className="link-button" disabled={!past.length} onClick={undo} title="Undo (Ctrl+Z)">Undo</button>
        <button type="button" className="link-button" disabled={!future.length} onClick={redo} title="Redo (Ctrl+Shift+Z)">Redo</button>
        <label className="designer-zoom">
          Zoom
          <input type="range" min={0.5} max={2} step={0.1} value={zoom} onChange={e => setZoom(Number(e.target.value))} />
          <span>{Math.round(zoom * 100)}%</span>
        </label>
        <button type="button" className="secondary designer-btn" onClick={close}>Close</button>
        <button type="button" className="primary designer-btn" disabled={saving || !dirty} onClick={save}>
          {saving ? "Saving…" : dirty ? "Save design" : "Saved"}
        </button>
      </div>
    </div>

    {error && <div className="form-error" style={{ marginBottom: 10 }}>{error}</div>}
    {warning && <div className="designer-warning">{warning}</div>}
    {batchArtworkPreview && <div className="designer-warning">
      Previewing the current batch image. Saving this design changes only the layout; it does not replace the shared template artwork.
    </div>}

    <div className="designer-layout">
      <div className="designer-stage">
        <div className="designer-toolbar">
          <span className="designer-toolbar-label">Add</span>
          {catalogue.palette.filter(entry => entry.type !== "field").map(entry => (
            <button key={entry.type} type="button" className="chip" title={entry.hint}
                    onClick={() => addElement(entry.type)}>+ {entry.label}</button>
          ))}
          <div className="designer-menu-wrap">
            <button type="button" className="chip" onClick={() => setAddingField(open => !open)}>+ Form field ▾</button>
            {addingField && <div className="designer-menu">
              <p>Insert a placeholder filled from the batch form when previewed and printed</p>
              {catalogue.variables.map(variable => (
                <button key={variable.key} type="button" onClick={() => addElement("field", variable.key)}>
                  <strong>{variable.label}</strong><small>{variable.sample.split("\n")[0]}</small>
                </button>
              ))}
            </div>}
          </div>
          {active && <>
            <span className="designer-toolbar-label">Align</span>
            {([["left", "⇤"], ["hcenter", "↔"], ["right", "⇥"], ["top", "⇡"], ["vcenter", "↕"], ["bottom", "⇣"]] as const)
              .map(([edge, glyph]) => (
                <button key={edge} type="button" className="chip" title={`Align ${edge}`} onClick={() => alignTo(edge)}>{glyph}</button>
              ))}
          </>}
        </div>

        <div className="designer-canvas-wrap">
          <div ref={canvasRef} className="designer-canvas"
               onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}
               onPointerDown={() => setSelected(null)}>
            <CardPreview document={doc} artwork={artwork} width={card.w} height={card.h}
                         scale={displayScale} values={values}>
              {elements.map(element => {
                const isSelected = element.id === selected;
                const width = (element.w || 0) * displayScale;
                const height = element.type === "barcode"
                  ? ((element.h || 0) + (element.show_value !== false ? (element.value_size || 7) * 1.4 : 0)) * displayScale
                  : (element.h || 0) * displayScale;
                const box = element.type === "text" || element.type === "field";
                // Text with no fixed width is only as wide as what it prints, so
                // the drag target has to be measured from the rendered string.
                const printed = box ? elementText(element, values).split("\n")
                  .reduce((longest, line) => line.length > longest.length ? line : longest, "") : "";
                return <div key={element.id}
                  className={"designer-hit" + (isSelected ? " selected" : "") + (element.hidden ? " hidden" : "")}
                  style={{
                    left: element.x * displayScale,
                    top: element.y * displayScale,
                    width: width || (box ? Math.max(16, printed.length * (element.size || 8) * 0.52 * displayScale) : 10),
                    height: height || (box ? (element.line_height || (element.size || 8) + 2)
                      * Math.max(1, elementText(element, values).split("\n").length) * displayScale : 10),
                    transform: box && !element.w && element.align === "center" ? "translateX(-50%)"
                             : box && !element.w && element.align === "right" ? "translateX(-100%)" : undefined,
                  }}
                  onPointerDown={event => startDrag(event, element.id, "move")}>
                  {isSelected && !element.hidden && HANDLES.map(handle => (
                    <span key={handle} className={`designer-handle h-${handle}`}
                          onPointerDown={event => startDrag(event, element.id, handle)} />
                  ))}
                </div>;
              })}
            </CardPreview>
          </div>
          <p className="designer-hint">
            Drag to move · drag a corner to resize · arrow keys nudge (Shift = 10pt) · Ctrl+D duplicates ·
            Delete removes · hold Alt for finer positioning
          </p>
        </div>

        <div className="designer-proof">
          <div className="designer-proof-head">
            <label className="checkbox-row">
              <input type="checkbox" checked={showProof} onChange={e => setShowProof(e.target.checked)} />
              Live PDF proof {proofing && <em>· rendering…</em>}
            </label>
            <span>Exactly what prints, with sample values.</span>
          </div>
          {showProof && proofUrl && <iframe src={proofUrl} title="PDF proof" className="designer-proof-frame" />}
          {showProof && !proofUrl && <div className="data-state">Rendering the first proof…</div>}
        </div>
      </div>

      <div className="designer-panel">
        <div className="designer-section">
          <p className="geo-panel-title">Layers <small>(top of the list prints last)</small></p>
          <ul className="designer-layers">
            {[...elements].reverse().map(element => (
              <li key={element.id} className={element.id === selected ? "selected" : ""}>
                <button type="button" className="designer-layer-name" onClick={() => setSelected(element.id)}>
                  <span className="designer-layer-type">{TYPE_GLYPHS[element.type]}</span>
                  {defaultName(element, catalogue.variables)}
                </button>
                <button type="button" className="designer-layer-icon" title={element.hidden ? "Show" : "Hide"}
                        onClick={() => setProp(element.id, { hidden: !element.hidden }, "")}>
                  {element.hidden ? "◌" : "●"}
                </button>
                <button type="button" className="designer-layer-icon" title="Bring forward"
                        onClick={() => reorder(element.id, 1)}>↑</button>
                <button type="button" className="designer-layer-icon" title="Send backward"
                        onClick={() => reorder(element.id, -1)}>↓</button>
              </li>
            ))}
          </ul>
          <div className="designer-row">
            <button type="button" className="link-button" onClick={clearCard}>Clear card</button>
            {catalogue.starters.map(starter => (
              <button key={starter.key} type="button" className="link-button" title={starter.description}
                      onClick={() => applyStarter(starter)}>Use {starter.label}</button>
            ))}
          </div>
        </div>

        {active && <div className="designer-section geo-props">
          <p className="geo-panel-title">{TYPE_LABELS[active.type]} — {defaultName(active, catalogue.variables)}</p>

          {active.type === "text" && <label>Text
            <textarea rows={3} value={active.text || ""}
                      onChange={e => setProp(active.id, { text: e.target.value }, `text:${active.id}`)} />
          </label>}

          {active.type === "field" && <>
            <label>Shows
              <select value={active.source || ""} onChange={e => setProp(active.id, { source: e.target.value }, "")}>
                {catalogue.variables.map(variable => <option key={variable.key} value={variable.key}>{variable.label}</option>)}
              </select>
            </label>
            <div className="designer-pair">
              <label>Before<input value={active.prefix || ""}
                onChange={e => setProp(active.id, { prefix: e.target.value }, `prefix:${active.id}`)} /></label>
              <label>After<input value={active.suffix || ""}
                onChange={e => setProp(active.id, { suffix: e.target.value }, `suffix:${active.id}`)} /></label>
            </div>
            <p className="designer-note">Prints “{elementText(active, values) || "—"}” on the sample.</p>
          </>}

          <div className="designer-pair">
            <label>X (pt)<input type="number" step="0.5" min={0} max={card.w} value={active.x}
              onChange={e => setProp(active.id, { x: Number(e.target.value) }, `x:${active.id}`)} /></label>
            <label>Y (pt)<input type="number" step="0.5" min={0} max={card.h} value={active.y}
              onChange={e => setProp(active.id, { y: Number(e.target.value) }, `y:${active.id}`)} /></label>
          </div>

          {(sized || typographic) && <div className="designer-pair">
            <label>Width (pt){typographic && <small> — 0 = fit text</small>}
              <input type="number" step="0.5" min={0} max={card.w} value={active.w ?? 0}
                     onChange={e => setProp(active.id, { w: Number(e.target.value) }, `w:${active.id}`)} /></label>
            {sized && <label>Height (pt)<input type="number" step="0.5" min={0} max={card.h} value={active.h ?? 0}
              onChange={e => setProp(active.id, { h: Number(e.target.value) }, `h:${active.id}`)} /></label>}
          </div>}

          {typographic && <>
            <div className="designer-pair">
              <label>Size (pt)<input type="number" step="0.5" min={1} value={active.size ?? 10}
                onChange={e => setProp(active.id, { size: Number(e.target.value) }, `size:${active.id}`)} /></label>
              <label>Line spacing<input type="number" step="0.5" min={1} value={active.line_height ?? ((active.size ?? 10) + 2)}
                onChange={e => setProp(active.id, { line_height: Number(e.target.value) }, `lh:${active.id}`)} /></label>
            </div>
            <label>Font
              <select value={active.font || "Helvetica"} onChange={e => setProp(active.id, { font: e.target.value }, "")}>
                {catalogue.fonts.map(font => <option key={font} value={font}>{font}</option>)}
              </select>
            </label>
            <div className="designer-pair">
              <label>Colour<input type="color" value={active.color || "#231B36"}
                onChange={e => setProp(active.id, { color: e.target.value }, `color:${active.id}`)} /></label>
              <label>Align
                <select value={active.align || "left"} onChange={e => setProp(active.id, { align: e.target.value as Align }, "")}>
                  {catalogue.alignments.map(align => <option key={align} value={align}>{align}</option>)}
                </select>
              </label>
            </div>
            <label>Maximum lines <small>(0 = no limit)</small>
              <input type="number" min={0} step={1} value={active.max_lines ?? 0}
                     onChange={e => setProp(active.id, { max_lines: Number(e.target.value) || undefined }, `ml:${active.id}`)} />
            </label>
          </>}

          {active.type === "box" && <>
            <div className="designer-pair">
              <label>Fill<input type="color" value={active.fill || "#FFFFFF"}
                onChange={e => setProp(active.id, { fill: e.target.value }, `fill:${active.id}`)} /></label>
              <label>Corner radius<input type="number" min={0} step={0.5} value={active.radius ?? 0}
                onChange={e => setProp(active.id, { radius: Number(e.target.value) }, `radius:${active.id}`)} /></label>
            </div>
            <label>Opacity ({Math.round((active.opacity ?? 1) * 100)}%)
              <input type="range" min={0} max={1} step={0.05} value={active.opacity ?? 1}
                     onChange={e => setProp(active.id, { opacity: Number(e.target.value) }, `opacity:${active.id}`)} />
            </label>
            <div className="designer-pair">
              <label>Border<input type="color" value={active.border_color || "#DCD7E8"}
                onChange={e => setProp(active.id, { border_color: e.target.value }, `bc:${active.id}`)} /></label>
              <label>Border width<input type="number" min={0} step={0.25} value={active.border_width ?? 0}
                onChange={e => setProp(active.id, { border_width: Number(e.target.value) }, `bw:${active.id}`)} /></label>
            </div>
          </>}

          {active.type === "line" && <label>Colour<input type="color" value={active.color || "#DCD7E8"}
            onChange={e => setProp(active.id, { color: e.target.value }, `color:${active.id}`)} /></label>}

          {active.type === "barcode" && <>
            <p className="designer-note">
              Mandatory. Encodes each voucher's own number, which is unique across the whole portal.
            </p>
            <label>Bar colour<input type="color" value={active.color || "#000000"}
              onChange={e => setProp(active.id, { color: e.target.value }, `color:${active.id}`)} /></label>
            <label className="checkbox-row">
              <input type="checkbox" checked={active.show_value !== false}
                     onChange={e => setProp(active.id, { show_value: e.target.checked }, "")} />
              Print the number underneath
            </label>
            {active.show_value !== false && <div className="designer-pair">
              <label>Number size<input type="number" min={4} step={0.5} value={active.value_size ?? 7}
                onChange={e => setProp(active.id, { value_size: Number(e.target.value) }, `vs:${active.id}`)} /></label>
              <label>Number colour<input type="color" value={active.value_color || "#231B36"}
                onChange={e => setProp(active.id, { value_color: e.target.value }, `vc:${active.id}`)} /></label>
            </div>}
          </>}

          {active.type !== "barcode" && <label>Only show when
            <select value={active.hide_if_empty || ""} onChange={e => setProp(active.id, { hide_if_empty: e.target.value || undefined }, "")}>
              <option value="">Always show</option>
              {catalogue.variables.map(variable => (
                <option key={variable.key} value={variable.key}>{variable.label} has a value</option>
              ))}
            </select>
          </label>}

          <div className="designer-row">
            <button type="button" className="link-button" onClick={() => duplicate(active.id)}>Duplicate</button>
            <button type="button" className="link-button" onClick={() => setProp(active.id, { hidden: !active.hidden }, "")}>
              {active.hidden ? "Show" : "Hide"}
            </button>
            <button type="button" className="link-button danger" onClick={() => remove(active.id)}>Delete</button>
          </div>
        </div>}

        <div className="designer-section geo-props">
          <p className="geo-panel-title">Card</p>
          <label>Size
            <select value={catalogue.coupon_presets.find(p => p.w === card.w && p.h === card.h)?.key || "custom"}
                    onChange={e => {
                      const preset = catalogue.coupon_presets.find(p => p.key === e.target.value);
                      if (preset) applyCardSize(preset.w, preset.h);
                    }}>
              {catalogue.coupon_presets.map(preset => <option key={preset.key} value={preset.key}>{preset.label}</option>)}
              <option value="custom">Custom</option>
            </select>
          </label>
          <div className="designer-pair">
            <label>Width (pt)<input type="number" min={100} step={1} value={card.w}
              onChange={e => applyCardSize(Number(e.target.value) || card.w, card.h)} /></label>
            <label>Height (pt)<input type="number" min={60} step={1} value={card.h}
              onChange={e => applyCardSize(card.w, Number(e.target.value) || card.h)} /></label>
          </div>
          <label>Background<input type="color" value={doc.background || "#FFFFFF"}
            onChange={e => edit({ ...doc, background: e.target.value }, "background")} /></label>
          <label className="checkbox-row">
            <input type="checkbox" checked={!doc.artwork?.hidden}
                   onChange={e => edit({ ...doc, artwork: { x: 0, y: 0, w: card.w, h: card.h, ...doc.artwork, hidden: !e.target.checked } })} />
            Show background artwork
          </label>
          {canAdmin && <label>{artwork ? "Replace artwork" : "Upload artwork"} <small>(JPEG/PNG, matching the card's shape)</small>
            <input type="file" accept="image/png,image/jpeg" disabled={busy === "artwork"}
                   onChange={e => e.target.files?.[0] && uploadArtwork(e.target.files[0])} />
          </label>}
          {busy === "artwork" && <div className="data-state">Uploading…</div>}
          {canAdmin && <button type="button" className="link-button danger" disabled={saving} onClick={async () => {
            if (!window.confirm("Empty this card back to just the barcode? This saves immediately.")) return;
            setSaving(true);
            try {
              const updated = await fmsRequest<TemplateDetail>(`voucher-portal/templates/${template.id}/reset-geometry/`, { method: "POST", body: "{}" });
              const emptied = cardDocument(updated) || catalogue.blank;
              setDoc(emptied); setSaved(JSON.stringify(emptied)); setPast([]); setFuture([]); setSelected(null);
            } catch (err: any) { setError(parseApiError(err)); }
            finally { setSaving(false); }
          }}>Reset to an empty card</button>}
        </div>
      </div>
    </div>
  </section>;
}

/** A library card's miniature. Its own component so each one can fetch its
 *  artwork through the API independently. */
function CardThumbnail({ template, document: doc, width, height, values, display = 200 }: {
  template: Template; document: CardDocument | null; width: number; height: number;
  values: Record<string, string>; display?: number;
}) {
  const artwork = useArtwork(template.artwork_path);
  return <CardPreview document={doc} artwork={artwork} width={width} height={height}
                      scale={display / width} values={values} />;
}

function TemplatesScreen({ canAdmin, intent, onIntentDone }: {
  canAdmin: boolean;
  /** Set when the create-batch form sent the user here to design a card. */
  intent?: DesignIntent;
  onIntentDone?: (designedTemplateId?: number) => void;
}) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [layouts, setLayouts] = useState<Record<number, { layout: CardDocument | null; w: number; h: number }>>({});
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ name: "", preset: "adcoop" });
  const [file, setFile] = useState<File | null>(null);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [editing, setEditing] = useState<Template | null>(null);

  const load = async () => {
    setLoading(true); setLoadError("");
    try {
      const data = await fmsRequest<{ results: TemplateSummary[] } | TemplateSummary[]>(
        "voucher-portal/templates/?page_size=100");
      const list = unwrap(data);
      setTemplates(list);
      setLayouts(Object.fromEntries(list.map(t => [t.id, {
        layout: cardDocument(t), w: t.coupon_width || 479.52, h: t.coupon_height || 178,
      }])));
    } catch (error: any) {
      setLoadError(parseApiError(error));
    } finally { setLoading(false); }
  };

  useEffect(() => {
    load();
    fmsRequest<Catalogue>("voucher-portal/templates/field-catalogue/")
      .then(cat => setCatalogue(usableCatalogue(cat) ? cat : null))
      .catch(() => setCatalogue(null));
  }, []);

  useEffect(() => {
    if (!intent) return;
    if (intent.mode === "edit") setEditing(intent.template);
    else setShowNew(true);
  }, [intent]);

  /** Hands the user back to whatever sent them here. */
  const finishIntent = (templateId?: number) => onIntentDone?.(templateId);

  const sampleValues = useMemo(() => {
    const map: Record<string, string> = {};
    (catalogue?.variables || []).forEach(variable => { map[variable.key] = variable.sample; });
    return map;
  }, [catalogue]);

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

  /** Artwork is optional: a card can be designed from nothing but elements on
   *  a background colour, which is the point of the designer. */
  const createTemplate = async () => {
    setCreating(true); setCreateError("");
    try {
      const preset = catalogue?.coupon_presets.find(item => item.key === form.preset);
      const body = new FormData();
      body.append("name", form.name.trim() || "Untitled card");
      if (preset) {
        body.append("coupon_width", String(preset.w));
        body.append("coupon_height", String(preset.h));
      }
      if (file) body.append("artwork", file);
      const created = await fmsRequest<Template>("voucher-portal/templates/", { method: "POST", body });
      setShowNew(false); setForm({ name: "", preset: "adcoop" }); setFile(null);
      await load();
      setEditing(created);  // straight into the designer - that's what they came for
    } catch (error: any) { setCreateError(parseApiError(error)); }
    finally { setCreating(false); }
  };

  if (editing) {
    return <TemplateDesigner template={editing} canAdmin={canAdmin}
                             previewArtwork={intent?.previewArtwork}
                             onClose={() => {
                               const designed = editing.id;
                               setEditing(null);
                               if (intent) finishIntent(designed); else load();
                             }}
                             onSaved={load} />;
  }

  return <section className="voucher-card">
    <div className="voucher-card-head">
      <h2>Voucher cards</h2>
      <div className="designer-actions">
        {intent && <button type="button" className="secondary designer-btn" onClick={() => finishIntent()}>
          Back to the batch
        </button>}
        <button type="button" className="primary designer-btn" onClick={() => setShowNew(open => !open)}>
          {showNew ? "Cancel" : "New card design"}
        </button>
      </div>
    </div>

    {showNew && <div className="voucher-inline-form" style={{ marginBottom: 18 }}>
      <label className="designer-field">Name
        <input placeholder="e.g. Eid gift card" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
      </label>
      {catalogue && <label className="designer-field">Card size
        <select value={form.preset} onChange={e => setForm({ ...form, preset: e.target.value })}>
          {catalogue.coupon_presets.map(preset => (
            <option key={preset.key} value={preset.key}>{preset.label}</option>
          ))}
        </select>
      </label>}
      <label className="designer-field">Background artwork <small>(optional — design on a plain background if you prefer)</small>
        <input type="file" accept="image/png,image/jpeg" onChange={e => setFile(e.target.files?.[0] || null)} />
      </label>
      {createError && <div className="form-error">{createError}</div>}
      <button type="button" className="primary" disabled={creating} onClick={createTemplate}>
        {creating ? "Creating…" : "Create and start designing"}
      </button>
    </div>}

    {loadError && <div className="form-error" style={{ marginBottom: 14 }}>{loadError}</div>}
    {!loading && !loadError && !catalogue && <div className="designer-warning">{API_TOO_OLD}</div>}
    {loading && <div className="data-state">Loading…</div>}
    {!loading && <div className="voucher-template-grid">
      {templates.map(t => {
        const stored = layouts[t.id];
        const count = stored?.layout?.elements?.length ?? 0;
        return <div key={t.id} className="voucher-template-card">
          <div className="voucher-template-thumb">
            {stored?.layout ? <CardThumbnail template={t} document={stored.layout} width={stored.w}
                                             height={stored.h} values={sampleValues} />
                    : <div className="voucher-template-placeholder">Layout unavailable</div>}
          </div>
          <strong>{t.name}</strong>
          <div className="voucher-template-badges">
            {t.is_default && <span className="chip active">Default</span>}
            {!t.is_active && <span className="chip">Inactive</span>}
            {stored?.layout && <span className="chip">{count} element{count === 1 ? "" : "s"}</span>}
          </div>
          <div className="voucher-template-actions">
            <button type="button" className="link-button" onClick={() => setEditing(t)}>Design</button>
            {canAdmin && !t.is_default && <button type="button" className="link-button" disabled={busyId === t.id} onClick={() => setDefault(t)}>Set default</button>}
            {canAdmin && <button type="button" className="link-button" disabled={busyId === t.id} onClick={() => toggleActive(t)}>{t.is_active ? "Deactivate" : "Activate"}</button>}
          </div>
        </div>;
      })}
      {templates.length === 0 && <div className="data-state">No card designs yet — create one to get started.</div>}
    </div>}
  </section>;
}

/** Departments, voucher types and numbering prefixes.
 *
 *  These are what every batch is built out of, and until now the only way to
 *  add one was the database or the Django admin - which meant a new voucher
 *  type was a developer's errand. The API has always supported the writes
 *  (administrator-only); this is the screen for them. */
function SetupScreen({ onChanged }: { onChanged: () => Promise<void> | void }) {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [types, setTypes] = useState<(VoucherType & { department_name?: string; is_active?: boolean })[]>([]);
  const [prefixes, setPrefixes] = useState<(Prefix & { department_name?: string; voucher_type_name?: string; is_active?: boolean })[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState("");
  const [adding, setAdding] = useState<"department" | "type" | "prefix" | null>(null);

  const [deptForm, setDeptForm] = useState({ code: "", name: "" });
  const [typeForm, setTypeForm] = useState({ code: "", name: "", department: "" });
  const [prefixForm, setPrefixForm] = useState({ prefix: "", label: "", department: "", voucher_type: "", sequence_length: "4" });

  const load = async () => {
    setLoading(true);
    try {
      const [d, t, p] = await Promise.all([
        fmsRequest<{ results: Department[] } | Department[]>("voucher-portal/departments/?page_size=200"),
        fmsRequest<{ results: any[] } | any[]>("voucher-portal/voucher-types/?page_size=200"),
        fmsRequest<{ results: any[] } | any[]>("voucher-portal/prefixes/?page_size=200"),
      ]);
      setDepartments(unwrap(d)); setTypes(unwrap(t)); setPrefixes(unwrap(p));
    } catch (err: any) { setError(parseApiError(err)); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  /** Every write refreshes the create-batch form's own copy too, so a type
   *  added here is selectable there without a reload. */
  const write = async (key: string, path: string, body: any, method = "POST") => {
    setSaving(key); setError("");
    try {
      await fmsRequest(path, { method, body: JSON.stringify(body) });
      await load();
      await onChanged();
      return true;
    } catch (err: any) { setError(parseApiError(err)); return false; }
    finally { setSaving(""); }
  };

  const toggle = (path: string, row: { id: number; is_active?: boolean }) =>
    write(`toggle-${path}-${row.id}`, `${path}${row.id}/`, { is_active: !row.is_active }, "PATCH");

  const addDepartment = async () => {
    if (await write("department", "voucher-portal/departments/",
                    { code: deptForm.code.trim().toUpperCase(), name: deptForm.name.trim() })) {
      setDeptForm({ code: "", name: "" }); setAdding(null);
    }
  };
  const addType = async () => {
    if (await write("type", "voucher-portal/voucher-types/", {
      code: typeForm.code.trim().toUpperCase(), name: typeForm.name.trim(), department: typeForm.department,
    })) { setTypeForm({ code: "", name: "", department: "" }); setAdding(null); }
  };
  const addPrefix = async () => {
    if (await write("prefix", "voucher-portal/prefixes/", {
      prefix: prefixForm.prefix.trim().toUpperCase(), label: prefixForm.label.trim(),
      department: prefixForm.department, voucher_type: prefixForm.voucher_type,
      sequence_length: Number(prefixForm.sequence_length) || 4,
    })) { setPrefixForm({ prefix: "", label: "", department: "", voucher_type: "", sequence_length: "4" }); setAdding(null); }
  };

  const typesForDepartment = prefixForm.department
    ? types.filter(t => String(t.department) === prefixForm.department) : types;

  const statusCell = (row: { id: number; is_active?: boolean }, path: string) => <>
    <span className={"status " + (row.is_active === false ? "cancelled" : "approved")}>
      {row.is_active === false ? "inactive" : "active"}
    </span>
    <button type="button" className="link-button" style={{ marginLeft: 10 }}
            disabled={saving === `toggle-${path}-${row.id}`} onClick={() => toggle(path, row)}>
      {row.is_active === false ? "Reactivate" : "Deactivate"}
    </button>
  </>;

  if (loading) return <section className="voucher-card"><div className="data-state">Loading…</div></section>;

  return <>
    {error && <section className="voucher-card"><div className="form-error">{error}</div></section>}

    <section className="voucher-card">
      <div className="voucher-card-head">
        <h2>Departments</h2>
        <button type="button" className="primary designer-btn"
                onClick={() => setAdding(adding === "department" ? null : "department")}>
          {adding === "department" ? "Cancel" : "Add department"}
        </button>
      </div>
      {adding === "department" && <div className="setup-form">
        <label>Code <small>(short, unique)</small>
          <input value={deptForm.code} maxLength={20} placeholder="HR"
                 onChange={e => setDeptForm({ ...deptForm, code: e.target.value })} /></label>
        <label>Name<input value={deptForm.name} placeholder="Human Resources"
                          onChange={e => setDeptForm({ ...deptForm, name: e.target.value })} /></label>
        <button type="button" className="primary" disabled={saving === "department" || !deptForm.code.trim() || !deptForm.name.trim()}
                onClick={addDepartment}>{saving === "department" ? "Adding…" : "Add"}</button>
      </div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Code</th><th>Status</th></tr></thead>
          <tbody>
            {departments.map(d => <tr key={d.id}>
              <td><strong>{d.name}</strong></td><td>{d.code}</td>
              <td>{statusCell(d as any, "voucher-portal/departments/")}</td>
            </tr>)}
            {departments.length === 0 && <tr><td colSpan={3}><div className="data-state">None yet.</div></td></tr>}
          </tbody>
        </table>
      </div>
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head">
        <h2>Voucher types</h2>
        <button type="button" className="primary designer-btn" disabled={departments.length === 0}
                onClick={() => setAdding(adding === "type" ? null : "type")}>
          {adding === "type" ? "Cancel" : "Add voucher type"}
        </button>
      </div>
      {departments.length === 0 && <div className="data-state">Add a department first — a type belongs to one.</div>}
      {adding === "type" && <div className="setup-form">
        <label>Code<input value={typeForm.code} maxLength={20} placeholder="EMP"
                          onChange={e => setTypeForm({ ...typeForm, code: e.target.value })} /></label>
        <label>Name<input value={typeForm.name} placeholder="Employee Voucher"
                          onChange={e => setTypeForm({ ...typeForm, name: e.target.value })} /></label>
        <label>Department
          <select value={typeForm.department} onChange={e => setTypeForm({ ...typeForm, department: e.target.value })}>
            <option value="">Select…</option>
            {departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select></label>
        <button type="button" className="primary"
                disabled={saving === "type" || !typeForm.code.trim() || !typeForm.name.trim() || !typeForm.department}
                onClick={addType}>{saving === "type" ? "Adding…" : "Add"}</button>
      </div>}
      <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Code</th><th>Department</th><th>Status</th></tr></thead>
          <tbody>
            {types.map(t => <tr key={t.id}>
              <td><strong>{t.name}</strong></td><td>{t.code}</td>
              <td>{t.department_name || departments.find(d => d.id === t.department)?.name || "—"}</td>
              <td>{statusCell(t as any, "voucher-portal/voucher-types/")}</td>
            </tr>)}
            {types.length === 0 && <tr><td colSpan={4}><div className="data-state">None yet.</div></td></tr>}
          </tbody>
        </table>
      </div>
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head">
        <h2>Numbering prefixes</h2>
        <button type="button" className="primary designer-btn" disabled={types.length === 0}
                onClick={() => setAdding(adding === "prefix" ? null : "prefix")}>
          {adding === "prefix" ? "Cancel" : "Add prefix"}
        </button>
      </div>
      {types.length === 0 && <div className="data-state">Add a voucher type first — a prefix numbers one type.</div>}
      {adding === "prefix" && <div className="setup-form">
        <label>Prefix<input value={prefixForm.prefix} maxLength={20} placeholder="EMP"
                            onChange={e => setPrefixForm({ ...prefixForm, prefix: e.target.value })} /></label>
        <label>Label<input value={prefixForm.label} placeholder="Employee vouchers"
                           onChange={e => setPrefixForm({ ...prefixForm, label: e.target.value })} /></label>
        <label>Department
          <select value={prefixForm.department}
                  onChange={e => setPrefixForm({ ...prefixForm, department: e.target.value, voucher_type: "" })}>
            <option value="">Select…</option>
            {departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select></label>
        <label>Voucher type
          <select value={prefixForm.voucher_type}
                  onChange={e => setPrefixForm({ ...prefixForm, voucher_type: e.target.value })}>
            <option value="">Select…</option>
            {typesForDepartment.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select></label>
        <label>Digits <small>(EMP + 4 digits → EMP0001)</small>
          <input type="number" min={2} max={10} value={prefixForm.sequence_length}
                 onChange={e => setPrefixForm({ ...prefixForm, sequence_length: e.target.value })} /></label>
        <button type="button" className="primary"
                disabled={saving === "prefix" || !prefixForm.prefix.trim() || !prefixForm.label.trim()
                          || !prefixForm.department || !prefixForm.voucher_type}
                onClick={addPrefix}>{saving === "prefix" ? "Adding…" : "Add"}</button>
      </div>}
      <p className="designer-note" style={{ marginBottom: 10 }}>
        Each prefix owns one running sequence. “Next” is the number the following voucher will take — it only ever
        moves forward, which is what stops two batches from printing the same code.
      </p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Prefix</th><th>Label</th><th>Department</th><th>Type</th><th>Digits</th><th>Next</th><th>Status</th></tr></thead>
          <tbody>
            {prefixes.map(p => <tr key={p.id}>
              <td><strong>{p.prefix}</strong></td><td>{p.label}</td>
              <td>{p.department_name || departments.find(d => d.id === p.department)?.name || "—"}</td>
              <td>{p.voucher_type_name || types.find(t => t.id === p.voucher_type)?.name || "—"}</td>
              <td>{p.sequence_length}</td>
              <td>{String(p.next_sequence).padStart(p.sequence_length, "0")}</td>
              <td>{statusCell(p as any, "voucher-portal/prefixes/")}</td>
            </tr>)}
            {prefixes.length === 0 && <tr><td colSpan={7}><div className="data-state">None yet.</div></td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  </>;
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
