"use client";

import { useState } from "react";

const nav = ["Overview", "Customers", "Sales", "Operations", "Fleet", "Settlements", "Invoices", "Modules"];
const icons = ["⌂", "◇", "↗", "▦", "▱", "₹", "▤", "⊞"];

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

const modules: Record<string, { eyebrow: string; title: string; action: string; stats: string[][]; columns: string[]; rows: string[][] }> = {
  Customers: {
    eyebrow: "CUSTOMER MASTER", title: "Customers & KYC", action: "+ Add customer",
    stats: [["Active customers", "128", "+8 this month"], ["KYC pending", "3", "Needs attention"], ["Credit exposure", "₹18.4L", "Across 11 customers"]],
    columns: ["Customer", "GSTIN", "Credit limit", "Outstanding", "KYC status"],
    rows: [["Tata Consumer Products", "27AAACT2727Q1ZW", "₹8.0L", "₹2.4L", "Verified"], ["Asian Paints Ltd", "27AAACA3622K1ZV", "₹5.0L", "₹1.1L", "Verified"], ["V-Guard Industries", "32AAACV8098A1ZP", "₹3.5L", "₹84,000", "Pending"], ["Havells India", "07AAACH0351E1Z1", "₹6.0L", "₹0", "Verified"]]
  },
  Sales: {
    eyebrow: "SALES PIPELINE", title: "Quotes & contracts", action: "+ New quotation",
    stats: [["Open quotations", "16", "Worth ₹12.8L"], ["Won this month", "₹8.6L", "72% conversion"], ["Rate contracts", "24", "5 expiring soon"]],
    columns: ["Quote", "Customer", "Lane", "Freight", "Stage"],
    rows: [["QTN-1084", "Tata Consumer", "Mumbai → Pune", "₹42,800", "Negotiation"], ["QTN-1081", "Asian Paints", "Delhi → Jaipur", "₹36,250", "Accepted"], ["QTN-1079", "V-Guard", "Bengaluru → Chennai", "₹51,400", "Sent"], ["QTN-1076", "Havells India", "Gurugram → Lucknow", "₹64,200", "Draft"]]
  },
  Operations: {
    eyebrow: "TRANSPORT OPERATIONS", title: "LR, manifests & trips", action: "+ Book LR",
    stats: [["LRs today", "24", "18 dispatched"], ["Active trips", "32", "4 need attention"], ["POD pending", "7", "₹3.2L billable"]],
    columns: ["LR number", "Consignor → Consignee", "Trip sheet", "Vehicle", "Status"],
    rows: [["LR-240831", "Tata Consumer → D-Mart Pune", "TS-2841", "MH 04 JU 9182", "In transit"], ["LR-240829", "Asian Paints → Jaipur Depot", "TS-2839", "HR 55 AN 4021", "Loading"], ["LR-240826", "V-Guard → Chennai DC", "TS-2834", "KA 51 MN 6814", "Delayed"], ["LR-240822", "Havells → Lucknow Hub", "TS-2831", "UP 32 KL 1098", "Delivered"]]
  },
  Fleet: {
    eyebrow: "OWN FLEET", title: "Vehicles & trip costing", action: "+ Add vehicle",
    stats: [["Fleet size", "41", "32 on road"], ["Cost per km", "₹28.40", "↓ ₹1.20 vs July"], ["Maintenance due", "5", "2 critical"]],
    columns: ["Vehicle", "Type", "Driver", "Month running", "Cost / km"],
    rows: [["MH 04 JU 9182", "32 ft MXL", "Ramesh Yadav", "6,842 km", "₹27.80"], ["HR 55 AN 4021", "22 ft SXL", "Sandeep Kumar", "5,106 km", "₹29.10"], ["KA 51 MN 6814", "32 ft MXL", "Vijay Raj", "7,214 km", "₹28.60"], ["GJ 01 KT 7730", "20 ft", "Irfan Sheikh", "4,832 km", "₹26.90"]]
  },
  Settlements: {
    eyebrow: "DRIVER ACCOUNTS", title: "Driver settlements", action: "+ New settlement",
    stats: [["Pending settlement", "₹1.84L", "Across 8 drivers"], ["Trip advances", "₹96,000", "11 open advances"], ["Settled this month", "₹7.2L", "42 settlements"]],
    columns: ["Driver", "Trip sheet", "Advance", "Expenses", "Net payable"],
    rows: [["Ramesh Yadav", "TS-2841", "₹12,000", "₹18,450", "₹6,450 due"], ["Sandeep Kumar", "TS-2839", "₹10,000", "₹9,240", "₹760 recover"], ["Vijay Raj", "TS-2834", "₹15,000", "₹21,180", "₹6,180 due"], ["Irfan Sheikh", "TS-2836", "₹8,000", "₹11,620", "₹3,620 due"]]
  },
  Invoices: {
    eyebrow: "BILLING & COLLECTIONS", title: "Customer invoices", action: "+ Generate invoice",
    stats: [["Unbilled trips", "₹3.2L", "7 PODs received"], ["Outstanding", "₹12.8L", "₹5.6L overdue"], ["Collected this month", "₹18.6L", "92% of target"]],
    columns: ["Invoice", "Customer", "Invoice date", "Amount", "Payment status"],
    rows: [["INV-2026-0842", "Tata Consumer", "02 Aug 2026", "₹2,48,600", "Payment due"], ["INV-2026-0838", "Asian Paints", "30 Jul 2026", "₹1,86,250", "Paid"], ["INV-2026-0831", "V-Guard", "26 Jul 2026", "₹3,12,400", "Overdue"], ["INV-2026-0824", "Havells India", "18 Jul 2026", "₹2,74,800", "Part paid"]]
  }
};

const featureGroups = [
  ["01", "Transport operations", "Consignments, job slips, dispatch, LR/bilty, ePOD, arrivals, routes, allocation, tracking, alerts and trip closure"],
  ["02", "Freight & billing", "Freight calculation, bills, debit/credit notes, detention, tolls, penalties, contracts and profitability"],
  ["03", "Collections", "Payment links, partial and multi-bill payments, TDS, disputes, reconciliation and outstanding tracking"],
  ["04", "Settlements", "Vendor, driver and transporter settlements, vouchers, approvals and reconciliation"],
  ["05", "Fleet payments — FPAY", "Bulk payouts, UPI, NEFT, RTGS, approval workflows, receipts and complete audit trails"],
  ["06", "Fleet maintenance", "Work orders, preventive service, breakdowns, tyres, batteries, spares, downtime and maintenance cost"],
  ["07", "Fleet master data", "Vehicle, driver, vendor, customer, route and asset masters with availability and utilisation"],
  ["08", "Financial management", "Trip, route, fleet and asset P&L with revenue, expense, cost-centre and ledger visibility"],
  ["09", "Accounting integrations", "Tally, Zoho Books, APIs and automatic accounting synchronisation"],
  ["10", "Reports & analytics", "40+ reports, scheduled reports, dashboards, KPIs and custom analytics"],
  ["11", "Compliance", "Vehicle and driver documents, reminders, expiry alerts and audit-ready records"],
  ["12", "Digital documentation", "ePOD, digital LR, bilty, photos, signatures, geo-tags, timestamps and cloud archive"],
  ["13", "Workflow automation", "AI agents for operations, collections, reconciliation, settlements and payment matching"],
  ["14", "Integrations", "150+ connections including SAP, Oracle, ULIP, OEM telematics, gateways and open APIs"],
  ["15", "Business intelligence", "Real-time profitability, revenue visibility, leakage detection and operational dashboards"],
];

function FeatureHub({ onAction }: { onAction: (message: string) => void }) {
  return <div className="module-page feature-page"><div className="module-title"><div><p className="eyebrow">COMPLETE TRANSPORT ERP</p><h2>One platform. Every fleet workflow.</h2><p>High-level capability map for modern Indian fleet owners and transporters.</p></div><button className="primary module-action" onClick={() => onAction("Capability brief exported")}>⇩ Export brief</button></div><div className="feature-grid">{featureGroups.map(group => <button className="feature-card" key={group[0]} onClick={() => onAction(`${group[1]} module opened`)}><span>{group[0]}</span><div><strong>{group[1]}</strong><p>{group[2]}</p></div><b>→</b></button>)}</div></div>;
}

function ModuleView({ name, onAction }: { name: string; onAction: (message: string) => void }) {
  const data = modules[name];
  const [query, setQuery] = useState("");
  const visibleRows = data.rows.filter(row => row.join(" ").toLowerCase().includes(query.toLowerCase()));
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">{data.eyebrow}</p><h2>{data.title}</h2><p>Manage every step from one operational workspace.</p></div><button className="primary module-action" onClick={() => onAction(`${data.action.replace("+ ", "")} opened`)}>{data.action}</button></div>
    <div className="module-stats">{data.stats.map(stat => <div className="module-stat" key={stat[0]}><span>{stat[0]}</span><strong>{stat[1]}</strong><small>{stat[2]}</small></div>)}</div>
    <section className="module-table-card"><div className="module-toolbar"><div><strong>All {name.toLowerCase()}</strong><span>{visibleRows.length} records</span></div><div className="toolbar-actions"><input aria-label={`Search ${name}`} placeholder={`Search ${name.toLowerCase()}...`} value={query} onChange={e => setQuery(e.target.value)} /><button onClick={() => onAction("Filters applied")}>☷ Filter</button><button onClick={() => onAction("Report exported")}>⇩ Export</button></div></div>
      <div className="table-wrap"><table><thead><tr>{data.columns.map(col => <th key={col}>{col}</th>)}<th>Action</th></tr></thead><tbody>{visibleRows.map((row, i) => <tr key={row[0]}>{row.map((cell, j) => <td key={cell}>{j === 0 ? <strong>{cell}</strong> : j === row.length - 1 ? <span className={`status ${cell.toLowerCase().replaceAll(" ", "-")}`}>{cell}</span> : cell}</td>)}<td><button className="row-action" onClick={() => onAction(`${row[0]} opened`)}>View →</button></td></tr>)}</tbody></table></div>
    </section>
  </div>;
}

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
              {item === "Customers" && <span className="badge">3</span>}
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

        {active === "Overview" ? <div className="page-grid">
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
        </div> : active === "Modules" ? <FeatureHub onAction={show} /> : <ModuleView name={active as keyof typeof modules} onAction={show} />}
      </section>
      {toast && <div className="toast">✓ {toast}</div>}
    </main>
  );
}

