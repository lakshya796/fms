"use client";

import { useState } from "react";

const nav = ["Overview", "Customers", "Sales", "Operations", "Fleet", "Settlements", "Invoices"];
const icons = ["⌂", "◇", "↗", "▦", "▱", "₹", "▤"];

const trips = [
  { id: "TRP-2841", route: "Mumbai → Pune", truck: "MH 04 JU 9182", driver: "Ramesh Yadav", status: "In transit", eta: "Today, 16:40", revenue: "₹42,800" },
  { id: "TRP-2839", route: "Delhi → Jaipur", truck: "HR 55 AN 4021", driver: "Sandeep Kumar", status: "Loading", eta: "Today, 19:15", revenue: "₹36,250" },
  { id: "TRP-2836", route: "Ahmedabad → Surat", truck: "GJ 01 KT 7730", driver: "Irfan Sheikh", status: "Delivered", eta: "POD received", revenue: "₹28,600" },
  { id: "TRP-2834", route: "Bengaluru → Chennai", truck: "KA 51 MN 6814", driver: "Vijay Raj", status: "Delayed", eta: "+ 2h 10m", revenue: "₹51,400" },
];

const workflows = [
  { name: "Customer KYC", detail: "GSTIN, PAN & credit checks", value: "3 pending", accent: "amber" },
  { name: "LR bookings", detail: "Consignments booked today", value: "24 active", accent: "blue" },
  { name: "Trip sheets", detail: "Ready for dispatch", value: "8 ready", accent: "violet" },
  { name: "Driver settlements", detail: "Advances & trip expenses", value: "₹1.84L due", accent: "green" },
];

export default function Home() {
  const [active, setActive] = useState("Overview");
  const [toast, setToast] = useState("");

  const show = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2200);
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark">p</span><span>phloz</span></div>
        <div className="workspace"><span className="workspace-avatar">RF</span><div><strong>Rajput Fleet</strong><small>Transport ERP</small></div><span className="chevron">⌄</span></div>
        <nav>
          {nav.map((item, i) => (
            <button key={item} className={active === item ? "nav-item active" : "nav-item"} onClick={() => { setActive(item); show(`${item} view selected`); }}>
              <span className="nav-icon">{icons[i]}</span>{item}
              {item === "Customer" && <span className="badge">3</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button className="nav-item"><span className="nav-icon">⚙</span>Settings</button>
          <div className="user"><span className="avatar">AK</span><div><strong>Arjun Kapoor</strong><small>Fleet owner</small></div><span>•••</span></div>
        </div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div><p className="eyebrow">MONDAY, 3 AUGUST</p><h1>{active === "Overview" ? "Good afternoon, Arjun" : active}</h1></div>
          <div className="top-actions"><button className="icon-button" aria-label="Search">⌕</button><button className="icon-button notification" aria-label="Notifications">♢</button><button className="primary" onClick={() => show("New LR booking opened")}>＋ New LR booking</button></div>
        </header>

        <div className="page-grid">
          <section className="hero-card">
            <div><span className="live-pill"><i /> LIVE FLEET</span><h2>32 of 41 vehicles<br />are on the road</h2><p>78% fleet utilisation · 4 trips need attention</p><button className="text-button" onClick={() => show("Live operations opened")}>View live operations <span>→</span></button></div>
            <div className="fleet-visual" aria-label="Fleet utilisation 78 percent"><div className="ring"><strong>78%</strong><span>utilised</span></div><div className="route-line"><span className="pin one" /><span className="truck">▰</span><span className="pin two" /></div></div>
          </section>

          <section className="metric-card"><div className="metric-top"><span className="metric-icon green">₹</span><span className="trend up">↗ 12.4%</span></div><p>Revenue this month</p><h3>₹28.6L</h3><small>vs ₹25.4L last month</small></section>
          <section className="metric-card"><div className="metric-top"><span className="metric-icon blue">↗</span><span className="trend down">↘ 3.1%</span></div><p>Operating cost</p><h3>₹19.2L</h3><small>Fuel is 61% of total cost</small></section>
          <section className="metric-card profit"><div className="metric-top"><span className="metric-icon violet">◎</span><span className="trend up">↗ 2.8%</span></div><p>Fleet margin</p><h3>32.9%</h3><small>₹9.4L gross contribution</small></section>

          <section className="workflow-card">
            <div className="section-heading"><div><p className="eyebrow">TODAY&apos;S WORKFLOW</p><h2>Keep operations moving</h2></div><button className="more">•••</button></div>
            <div className="workflow-list">{workflows.map((flow, i) => <button key={flow.name} className="workflow-row" onClick={() => show(`${flow.name} opened`)}><span className={`step ${flow.accent}`}>{String(i + 1).padStart(2,"0")}</span><span className="workflow-copy"><strong>{flow.name}</strong><small>{flow.detail}</small></span><span className={`flow-value ${flow.accent}`}>{flow.value}</span><span className="arrow">→</span></button>)}</div>
          </section>

          <section className="cash-card">
            <div className="section-heading"><div><p className="eyebrow">CASH POSITION</p><h2>Receivables</h2></div><button className="more">•••</button></div>
            <div className="donut-wrap"><div className="donut"><div><strong>₹12.8L</strong><span>outstanding</span></div></div></div>
            <div className="legend"><div><span><i className="dot current"/>Current</span><strong>₹7.2L</strong></div><div><span><i className="dot overdue"/>Overdue</span><strong>₹3.9L</strong></div><div><span><i className="dot critical"/>60+ days</span><strong>₹1.7L</strong></div></div>
            <button className="secondary" onClick={() => show("Invoice follow-ups opened")}>Review collections</button>
          </section>

          <section className="trips-card">
            <div className="section-heading"><div><p className="eyebrow">ACTIVE MOVEMENT</p><h2>Recent trips</h2></div><button className="link-button" onClick={() => show("All trips opened")}>View all trips →</button></div>
            <div className="table-wrap"><table><thead><tr><th>Trip & route</th><th>Vehicle</th><th>Driver</th><th>Status</th><th>ETA / POD</th><th>Revenue</th></tr></thead><tbody>{trips.map(t => <tr key={t.id}><td><strong>{t.id}</strong><small>{t.route}</small></td><td>{t.truck}</td><td>{t.driver}</td><td><span className={`status ${t.status.toLowerCase().replace(" ","-")}`}>{t.status}</span></td><td>{t.eta}</td><td><strong>{t.revenue}</strong></td></tr>)}</tbody></table></div>
          </section>
        </div>
      </section>
      {toast && <div className="toast">✓ {toast}</div>}
    </main>
  );
}
