"use client";

import { useEffect, useState } from "react";
import { fmsRequest } from "../lib/fms-api";

// Public consignee tracking, mirroring the Fleetbase order tracking experience.
// The API endpoint is open, so no login is required here.
export default function TrackConsignment() {
  const [number, setNumber] = useState("");
  const [order, setOrder] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const runSearch = async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setLoading(true); setError(""); setOrder(null);
    try {
      setOrder(await fmsRequest<any>(`track/${trimmed}/`));
    } catch {
      setError("We could not find a consignment with that tracking number.");
    } finally { setLoading(false); }
  };

  // A dispatcher can share a direct link (?number=...); land on it already searched.
  useEffect(() => {
    const shared = new URLSearchParams(window.location.search).get("number");
    if (shared) { setNumber(shared); runSearch(shared); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const search = (event: React.FormEvent) => { event.preventDefault(); runSearch(number); };

  const shareLink = async () => {
    const link = `${window.location.origin}${window.location.pathname}?number=${number.trim()}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable - the link is still visible in the address bar */ }
  };

  const progress = Math.max(0, Math.min(100, Number(order?.progress_percent || 0)));

  return <main className="track-page">
    <section className="track-card">
      <div className="brand login-brand"><span className="brand-mark">p</span><span>phloz</span></div>
      <p className="eyebrow">CONSIGNMENT TRACKING</p>
      <h1>Where is my shipment?</h1>
      <p className="track-intro">Enter the tracking number printed on your lorry receipt or shared over SMS.</p>
      <form onSubmit={search} className="track-form">
        <input value={number} onChange={event => setNumber(event.target.value)} placeholder="PHZ260804A1B2C3D4" aria-label="Tracking number" required />
        <button className="primary" disabled={loading || !number.trim()}>{loading ? "Searching…" : "Track"}</button>
      </form>
      {error && <div className="login-error">{error}</div>}

      {order && <div className="track-result">
        <div className="track-head">
          <div><strong>{order.number}</strong><small>{order.origin} → {order.destination}</small></div>
          <span className={"status " + String(order.status).replaceAll("_", "-")}>{String(order.status).replaceAll("_", " ")}</span>
        </div>

        {!["completed", "cancelled"].includes(order.status) && <div className="track-progress">
          <div className="track-progress-bar"><i style={{ width: `${Math.max(3, progress)}%` }} /></div>
          <div className="track-progress-labels"><span>{order.origin}</span><span>{progress}% of the way</span><span>{order.destination}</span></div>
        </div>}
        {order.last_position && <p className="track-last-seen">Last seen near <strong>{order.last_position.city || "an unnamed point"}</strong> · {new Date(order.last_position.recorded_at).toLocaleString("en-IN")}</p>}

        <div className="tracking-grid">
          <div><span>Consignor</span><strong>{order.customer_name}</strong></div>
          <div><span>Service</span><strong>{String(order.order_type).toUpperCase()}</strong></div>
          <div><span>Packages</span><strong>{order.packages}</strong></div>
          <div><span>Weight</span><strong>{Number(order.weight_kg).toLocaleString("en-IN")} kg</strong></div>
        </div>
        <div className="record-timeline">
          <p className="eyebrow">MOVEMENT HISTORY</p>
          {(order.activities || []).map((activity: any) => <div key={activity.id}>
            <i /><span><strong>{String(activity.code).replaceAll("_", " ")}</strong><small>{activity.details || activity.status}{activity.city ? ` · ${activity.city}` : ""}</small></span>
            <time>{new Date(activity.recorded_at).toLocaleString("en-IN")}</time>
          </div>)}
          {!(order.activities || []).length && <div><i /><span><strong>Booking confirmed</strong><small>Tracking updates will appear here</small></span><time>—</time></div>}
        </div>
        <button type="button" className="secondary track-share" onClick={shareLink}>{copied ? "Link copied" : "Share this tracking link"}</button>
      </div>}
      <small className="track-footer">Powered by Phloz Fleet — transport ERP for Indian fleet owners</small>
    </section>
  </main>;
}
