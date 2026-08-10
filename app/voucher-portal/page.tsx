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

  const submitIssue = async (voucher: Voucher) =>…23288 tokens truncated…it load();
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

