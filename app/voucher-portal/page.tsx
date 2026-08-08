"use client";

import { useEffect, useState } from "react";
import { fmsRequest, fmsRequestRaw, login, logout, UNAUTHORISED_EVENT } from "../lib/fms-api";

// Authenticated ADCOOP Voucher Portal - Phase 1 POC. Unlike /vouchers (deliberately
// public), every call here goes through fmsRequest/fmsRequestRaw with the same
// fms_token session the rest of the console uses.

type Department = { id: number; code: string; name: string };
type VoucherType = { id: number; code: string; name: string; department: number };
type Prefix = { id: number; prefix: string; label: string; department: number; voucher_type: number; sequence_length: number; next_sequence: number };
type Batch = {
  id: number; name: string; department_name: string; voucher_type_name: string; quantity: number;
  discount_type: string; display_value: string; currency: string; valid_from: string; valid_to: string;
  restrictions: string; terms: string; prefix_snapshot: string; status: string; combined_pdf_url: string;
  generation_error: string; generated_count: number; issued_count: number; created_at: string;
};
type Voucher = {
  id: number; batch: number; number: string; status: string; display_status: string;
  recipient_name: string; recipient_phone: string; recipient_email: string; recipient_reference: string;
  pdf_url: string; issued_at: string | null;
};

const EMPTY_FORM = {
  name: "", department: "", voucher_type: "", description: "", quantity: "10",
  discount_type: "fixed", percentage_value: "", max_discount_value: "", fixed_value: "", currency: "AED",
  valid_to: "", restrictions: "", terms: "", prefix: "",
};

function statusClass(status: string) {
  if (status === "generated" || status === "draft") return "unassigned";
  if (status === "generating") return "assigned";
  if (status === "failed" || status === "cancelled" || status === "expired") return "expired";
  return status;
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
  const [departments, setDepartments] = useState<Department[]>([]);
  const [voucherTypes, setVoucherTypes] = useState<VoucherType[]>([]);
  const [prefixes, setPrefixes] = useState<Prefix[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loadingBatches, setLoadingBatches] = useState(false);

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

  const loadReferenceData = async () => {
    const [d, t, p] = await Promise.all([
      fmsRequest<{ results: Department[] } | Department[]>("voucher-portal/departments/?page_size=200"),
      fmsRequest<{ results: VoucherType[] } | VoucherType[]>("voucher-portal/voucher-types/?page_size=200"),
      fmsRequest<{ results: Prefix[] } | Prefix[]>("voucher-portal/prefixes/?page_size=200"),
    ]);
    const list = <T,>(r: { results: T[] } | T[]) => (Array.isArray(r) ? r : r.results);
    setDepartments(list(d)); setVoucherTypes(list(t)); setPrefixes(list(p));
  };

  const loadBatches = async () => {
    setLoadingBatches(true);
    try {
      const data = await fmsRequest<{ results: Batch[] } | Batch[]>("voucher-portal/batches/?page_size=100");
      const list = Array.isArray(data) ? data : data.results;
      setBatches(list);
      if (activeBatch) {
        const refreshed = list.find(b => b.id === activeBatch.id);
        if (refreshed) setActiveBatch(refreshed);
      }
    } finally { setLoadingBatches(false); }
  };

  useEffect(() => { loadReferenceData(); loadBatches(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // While a batch is still assembling PDFs in the background, poll until it's ready.
  useEffect(() => {
    if (!batches.some(b => b.status === "generating")) return;
    const timer = setInterval(loadBatches, 2500);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batches]);

  const loadVouchers = async (batchId: number, status = "") => {
    const params = new URLSearchParams({ batch: String(batchId), page_size: "200" });
    if (status) params.set("status", status);
    const data = await fmsRequest<{ results: Voucher[] } | Voucher[]>(`voucher-portal/vouchers/?${params.toString()}`);
    setVouchers(Array.isArray(data) ? data : data.results);
  };

  const openBatch = async (batch: Batch) => {
    setActiveBatch(batch); setVoucherStatus(""); setCsvResult(null);
    await loadVouchers(batch.id);
  };

  const resetCreateForm = () => {
    setForm(EMPTY_FORM); setPreviewHash(""); setPreviewUrl(""); setPreviewError(""); setCreateError("");
    setArtworkPreviewUrl(""); setTemplateId(""); setArtworkError("");
  };

  const departmentPrefixes = prefixes.filter(p => !form.department || String(p.department) === form.department);
  const departmentTypes = voucherTypes.filter(t => !form.department || String(t.department) === form.department);

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

  const confirmGenerate = async () => {
    setCreating(true); setCreateError("");
    try {
      await fmsRequest("voucher-portal/batches/", { method: "POST", body: JSON.stringify({ ...buildPayload(), preview_hash: previewHash }) });
      setShowCreate(false); resetCreateForm(); loadBatches();
    } catch (error: any) {
      setCreateError(parseApiError(error));
    } finally { setCreating(false); }
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

  if (activeBatch) {
    return <main className="voucher-page">
      <header className="voucher-topbar">
        <div>
          <p className="voucher-eyebrow">VOUCHER PORTAL</p>
          <h1>{activeBatch.name}</h1>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
          <button className="secondary" onClick={() => setActiveBatch(null)}>Back to batches</button>
          <button className="secondary" onClick={onSignOut}>Sign out</button>
        </div>
      </header>

      <section className="voucher-card">
        <div className="voucher-card-head">
          <h2>{activeBatch.display_value} · {activeBatch.prefix_snapshot} · {activeBatch.generated_count} vouchers</h2>
          <span className={"status " + statusClass(activeBatch.status)}>{activeBatch.status}</span>
        </div>
        <p style={{ fontSize: 12, color: "var(--voucher-muted)" }}>
          Valid until {activeBatch.valid_to} · {activeBatch.issued_count} issued
        </p>
        {activeBatch.status === "generating" && <div className="data-state">Assembling PDFs in the background — this refreshes automatically.</div>}
        {activeBatch.status === "failed" && <div className="data-state error">Generation failed: {activeBatch.generation_error}</div>}
        {activeBatch.combined_pdf_url && <a className="secondary" style={{ display: "inline-block", padding: "8px 14px", borderRadius: 8, textDecoration: "none" }}
          href={activeBatch.combined_pdf_url} target="_blank" rel="noreferrer">Download print-ready PDF (all vouchers)</a>}
      </section>

      <section className="voucher-card">
        <div className="voucher-card-head">
          <h2>Issue recipients</h2>
        </div>
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
      </section>

      <section className="voucher-card">
        <div className="voucher-card-head"><h2>Vouchers</h2></div>
        <div className="voucher-tabs">
          {["", "generated", "issued", "expired"].map(s => (
            <button key={s} type="button" className={"chip" + (voucherStatus === s ? " active" : "")}
              onClick={() => { setVoucherStatus(s); loadVouchers(activeBatch.id, s); }}>{s || "All"}</button>
          ))}
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Number</th><th>Recipient</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
              {vouchers.map(v => <tr key={v.id}>
                <td><strong>{v.number}</strong></td>
                <td>{v.recipient_name || v.recipient_phone || v.recipient_email || "—"}</td>
                <td><span className={"status " + statusClass(v.display_status)}>{v.display_status}</span></td>
                <td className="voucher-actions">
                  {issuingId === v.id ? <div className="voucher-issue-inline">
                    <input value={issuePhone} onChange={e => setIssuePhone(e.target.value)} placeholder="Phone (optional)" autoFocus />
                    <button type="button" className="primary" onClick={() => submitIssue(v)}>Confirm</button>
                    <button type="button" className="link-button" onClick={() => setIssuingId(null)}>Cancel</button>
                    {issueError && <small className="voucher-issue-error">{issueError}</small>}
                  </div> : <>
                    {v.display_status === "generated" && <button type="button" className="link-button" onClick={() => startIssue(v)}>Issue</button>}
                    {v.pdf_url && <a className="link-button" href={v.pdf_url} target="_blank" rel="noreferrer">PDF</a>}
                  </>}
                </td>
              </tr>)}
              {vouchers.length === 0 && <tr><td colSpan={4}><div className="data-state">No vouchers in this view.</div></td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </main>;
  }

  return <main className="voucher-page">
    <header className="voucher-topbar">
      <div>
        <p className="voucher-eyebrow">STAFF ONLY</p>
        <h1>Voucher Portal</h1>
      </div>
      <div style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
        <button className="primary" onClick={() => { setShowCreate(v => !v); resetCreateForm(); }}>{showCreate ? "Close" : "New batch"}</button>
        <button className="secondary" onClick={onSignOut}>Sign out</button>
      </div>
    </header>

    {showCreate && <section className="voucher-card">
      <div className="voucher-card-head"><h2>Create a voucher batch</h2></div>
      <form className="voucher-form" onSubmit={e => e.preventDefault()}>
        <label>Voucher name<input required value={form.name} onChange={e => updateForm({ name: e.target.value })} /></label>
        <label>Quantity<input required type="number" min={1} max={10000} value={form.quantity} onChange={e => updateForm({ quantity: e.target.value })} /></label>

        <label>Department<select required value={form.department} onChange={e => updateForm({ department: e.target.value, voucher_type: "", prefix: "" })}>
          <option value="">Select…</option>
          {departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
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
          Review the design above. Changing any field above invalidates this preview.
        </p>
        {createError && <div className="form-error">{createError}</div>}
        <button className="primary" disabled={creating} onClick={confirmGenerate} style={{ width: "100%", height: 42 }}>
          {creating ? "Generating…" : `Are you sure you want to generate ${form.quantity} voucher(s)?`}
        </button>
      </div>}
    </section>}

    <section className="voucher-card">
      <div className="voucher-card-head"><h2>Batches</h2></div>
      {loadingBatches && <div className="data-state">Loading…</div>}
      {!loadingBatches && batches.length === 0 && <div className="data-state">No batches yet.</div>}
      {!loadingBatches && batches.length > 0 && <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Type</th><th>Value</th><th>Qty</th><th>Status</th><th>Issued</th><th></th></tr></thead>
          <tbody>
            {batches.map(b => <tr key={b.id} style={{ cursor: "pointer" }} onClick={() => openBatch(b)}>
              <td><strong>{b.name}</strong><small>{b.prefix_snapshot}</small></td>
              <td>{b.voucher_type_name}</td>
              <td>{b.display_value}</td>
              <td>{b.generated_count}</td>
              <td><span className={"status " + statusClass(b.status)}>{b.status}</span></td>
              <td>{b.issued_count}</td>
              <td><button type="button" className="link-button" onClick={e => { e.stopPropagation(); openBatch(b); }}>Open</button></td>
            </tr>)}
          </tbody>
        </table>
      </div>}
    </section>
  </main>;
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
