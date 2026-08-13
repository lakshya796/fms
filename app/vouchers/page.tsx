"use client";

import { useEffect, useState } from "react";
import { fmsRequest } from "../lib/fms-api";

// MAIR retail gift voucher desk. Every endpoint under vouchers/ is AllowAny -
// there is deliberately no login here, so this page never touches sessionStorage
// or the fms_token flow that the rest of the console relies on.
const API_BASE = process.env.NEXT_PUBLIC_FMS_API_URL || "https://api-test.phloz.app/fms";

type Voucher = {
  id: number;
  batch: number;
  batch_series: string;
  number: string;
  value: string;
  currency: string;
  valid_from: string;
  valid_to: string;
  phone_number: string;
  status: string;
  display_status: string;
  is_expired: boolean;
  issued_at: string | null;
  created_at: string;
};

type Summary = { total: number; unassigned: number; issued: number; expired: number };

const STATUS_TABS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "unassigned", label: "Unassigned" },
  { key: "issued", label: "Issued" },
  { key: "expired", label: "Expired" },
];

function formatDate(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-AE", { day: "2-digit", month: "short", year: "numeric" });
}

export default function GiftVoucherDesk() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [vouchers, setVouchers] = useState<Voucher[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");

  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ start: "", end: "", value: "", currency: "AED", valid_from: "", valid_to: "", created_by: "" });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [createResult, setCreateResult] = useState<{ first_number: string; last_number: string; count: number } | null>(null);

  const [issuingId, setIssuingId] = useState<number | null>(null);
  const [issuePhone, setIssuePhone] = useState("");
  const [issueError, setIssueError] = useState("");
  const [issueBusy, setIssueBusy] = useState(false);

  const pageSize = 50;
  const pageCount = Math.max(1, Math.ceil(count / pageSize));

  const loadSummary = async () => {
    try { setSummary(await fmsRequest<Summary>("vouchers/vouchers/summary/")); } catch { /* stat cards are non-critical */ }
  };

  const loadVouchers = async () => {
    setLoading(true); setListError("");
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
      if (status) params.set("status", status);
      if (search.trim()) params.set("search", search.trim());
      const data = await fmsRequest<{ count: number; results: Voucher[] }>(`vouchers/vouchers/?${params.toString()}`);
      setVouchers(data.results); setCount(data.count);
    } catch {
      setListError("Could not load vouchers. Please try again.");
    } finally { setLoading(false); }
  };

  useEffect(() => { loadSummary(); }, []);
  useEffect(() => { loadVouchers(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [page, status]);

  const runSearch = (event: React.FormEvent) => { event.preventDefault(); setPage(1); loadVouchers(); };
  const selectTab = (key: string) => { setStatus(key); setPage(1); };

  const createBatch = async (event: React.FormEvent) => {
    event.preventDefault();
    setCreating(true); setCreateError(""); setCreateResult(null);
    try {
      const result = await fmsRequest<{ first_number: string; last_number: string; count: number }>("vouchers/batches/", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setCreateResult(result);
      setForm({ start: "", end: "", value: "", currency: "AED", valid_from: "", valid_to: "", created_by: "" });
      setPage(1); setStatus("");
      loadSummary(); loadVouchers();
    } catch (error: any) {
      setCreateError(parseApiError(error));
    } finally { setCreating(false); }
  };

  const startIssue = (voucher: Voucher) => { setIssuingId(voucher.id); setIssuePhone(""); setIssueError(""); };
  const cancelIssue = () => { setIssuingId(null); setIssueError(""); };

  const submitIssue = async (voucher: Voucher) => {
    setIssueBusy(true); setIssueError("");
    try {
      await fmsRequest(`vouchers/vouchers/${voucher.id}/issue/`, { method: "POST", body: JSON.stringify({ phone_number: issuePhone }) });
      setIssuingId(null);
      loadSummary(); loadVouchers();
    } catch (error: any) {
      setIssueError(parseApiError(error));
    } finally { setIssueBusy(false); }
  };

  return <main className="voucher-page">
    <header className="voucher-topbar">
      <img src="/mair-logo.svg" alt="MAIR" className="voucher-logo" />
      <div>
        <p className="voucher-eyebrow">RETAIL OPERATIONS</p>
        <h1>Gift Voucher Desk</h1>
      </div>
    </header>

    <section className="voucher-stats">
      <div><span>Total</span><strong>{summary?.total ?? "—"}</strong></div>
      <div><span>Unassigned</span><strong>{summary?.unassigned ?? "—"}</strong></div>
      <div><span>Issued</span><strong>{summary?.issued ?? "—"}</strong></div>
      <div><span>Expired</span><strong>{summary?.expired ?? "—"}</strong></div>
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head">
        <h2>Create a voucher batch</h2>
        <button type="button" className="secondary" onClick={() => setShowCreate(v => !v)}>{showCreate ? "Close" : "New batch"}</button>
      </div>

      {showCreate && <form onSubmit={createBatch} className="voucher-form">
        <label>Starting voucher number<input required value={form.start} onChange={e => setForm({ ...form, start: e.target.value })} placeholder="GV-2026-000101" /></label>
        <label>Ending voucher number<input required value={form.end} onChange={e => setForm({ ...form, end: e.target.value })} placeholder="GV-2026-000150" /></label>
        <label>Voucher value<input required type="number" min="0.01" step="0.01" value={form.value} onChange={e => setForm({ ...form, value: e.target.value })} placeholder="100.00" /></label>
        <label>Currency<input value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value.toUpperCase() })} maxLength={8} /></label>
        <label>Validity start<input required type="date" value={form.valid_from} onChange={e => setForm({ ...form, valid_from: e.target.value })} /></label>
        <label>Validity end<input required type="date" value={form.valid_to} onChange={e => setForm({ ...form, valid_to: e.target.value })} /></label>
        <label className="voucher-form-wide">Created by <small>(optional)</small><input value={form.created_by} onChange={e => setForm({ ...form, created_by: e.target.value })} placeholder="Store manager name" /></label>
        {createError && <div className="form-error voucher-form-wide">{createError}</div>}
        <button className="primary voucher-form-wide" disabled={creating}>{creating ? "Generating…" : "Generate vouchers"}</button>
      </form>}

      {createResult && <div className="voucher-success">
        Generated {createResult.count} voucher{createResult.count === 1 ? "" : "s"}: <strong>{createResult.first_number}</strong> to <strong>{createResult.last_number}</strong>.
      </div>}
    </section>

    <section className="voucher-card">
      <div className="voucher-card-head">
        <h2>All vouchers</h2>
        <form onSubmit={runSearch} className="voucher-search">
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search number or phone" />
          <button className="secondary">Search</button>
        </form>
      </div>

      <div className="voucher-tabs">
        {STATUS_TABS.map(tab => <button key={tab.key} type="button" className={"chip" + (status === tab.key ? " active" : "")} onClick={() => selectTab(tab.key)}>{tab.label}</button>)}
      </div>

      {listError && <div className="data-state error">{listError}</div>}
      {!listError && loading && <div className="data-state">Loading…</div>}
      {!listError && !loading && vouchers.length === 0 && <div className="data-state">No vouchers match this view.</div>}

      {!listError && !loading && vouchers.length > 0 && <div className="table-wrap">
        <table>
          <thead><tr><th>Voucher</th><th>Value</th><th>Phone</th><th>Validity</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            {vouchers.map(voucher => <tr key={voucher.id}>
              <td><strong>{voucher.number}</strong><small>{voucher.batch_series}</small></td>
              <td>{voucher.currency} {Number(voucher.value).toLocaleString("en-AE", { minimumFractionDigits: 2 })}</td>
              <td>{voucher.phone_number || "—"}</td>
              <td>{formatDate(voucher.valid_from)} – {formatDate(voucher.valid_to)}</td>
              <td><span className={"status " + voucher.display_status}>{voucher.display_status}</span></td>
              <td className="voucher-actions">
                {issuingId === voucher.id ? <div className="voucher-issue-inline">
                  <input value={issuePhone} onChange={e => setIssuePhone(e.target.value)} placeholder="Phone (optional)" autoFocus />
                  <button type="button" className="primary" disabled={issueBusy} onClick={() => submitIssue(voucher)}>{issueBusy ? "…" : "Confirm"}</button>
                  <button type="button" className="link-button" onClick={cancelIssue}>Cancel</button>
                  {issueError && <small className="voucher-issue-error">{issueError}</small>}
                </div> : <>
                  {voucher.display_status === "unassigned" && <button type="button" className="link-button" onClick={() => startIssue(voucher)}>Issue</button>}
                  <a className="link-button" href={`${API_BASE}/api/v1/vouchers/vouchers/${voucher.id}/pdf/`} target="_blank" rel="noreferrer">PDF</a>
                </>}
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>}

      {pageCount > 1 && <div className="voucher-pagination">
        <button type="button" className="secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
        <span>Page {page} of {pageCount}</span>
        <button type="button" className="secondary" disabled={page >= pageCount} onClick={() => setPage(p => p + 1)}>Next</button>
      </div>}
    </section>

    <small className="voucher-footer">MAIR Retail — Gift Voucher Desk. This page requires no login; keep its link private to your store team.</small>
  </main>;
}

function parseApiError(error: any): string {
  const message = error?.message || "Something went wrong. Please try again.";
  try {
    const parsed = JSON.parse(message);
    if (typeof parsed === "string") return parsed;
    const first = Object.values(parsed)[0];
    return Array.isArray(first) ? String(first[0]) : String(first ?? message);
  } catch {
    return message;
  }
}
