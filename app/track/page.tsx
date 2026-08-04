"use client";

import { useState } from "react";
import { fmsRequest } from "../lib/fms-api";

// Public consignee tracking, mirroring the Fleetbase order tracking experience.
// The API endpoint is open, so no login is required here.
export default function TrackConsignment() {
  const [number, setNumber] = useState("");
  const [order, setOrder] = useState<any>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true); setError(""); setOrder(null);
    try {
      setOrder(await fmsRequest<any>(`track/${number.trim()}/`));
    } catch {
      setError("We could not find a consignment with that tracking number.");
    } finally { setLoading(false); }
  };

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
      </div>}
      <small className="track-footer">Powered by Phloz Fleet — transport ERP for Indian fleet owners</small>
    </section>
  </main>;
}
