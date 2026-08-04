"use client";

import { useEffect, useState } from "react";
import { fmsRequest, login } from "./lib/fms-api";

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
    columns: ["Customer", "GSTIN", "Credit limit", "Email", "KYC status"],
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
    columns: ["LR number", "Consignor → Consignee", "Route", "E-way bill", "Status"],
    rows: [["LR-240831", "Tata Consumer → D-Mart Pune", "TS-2841", "MH 04 JU 9182", "In transit"], ["LR-240829", "Asian Paints → Jaipur Depot", "TS-2839", "HR 55 AN 4021", "Loading"], ["LR-240826", "V-Guard → Chennai DC", "TS-2834", "KA 51 MN 6814", "Delayed"], ["LR-240822", "Havells → Lucknow Hub", "TS-2831", "UP 32 KL 1098", "Delivered"]]
  },
  Fleet: {
    eyebrow: "OWN FLEET", title: "Vehicles & trip costing", action: "+ Add vehicle",
    stats: [["Fleet size", "41", "32 on road"], ["Cost per km", "₹28.40", "↓ ₹1.20 vs July"], ["Maintenance due", "5", "2 critical"]],
    columns: ["Vehicle", "Type", "Ownership", "Capacity", "Status"],
    rows: [["MH 04 JU 9182", "32 ft MXL", "Ramesh Yadav", "6,842 km", "₹27.80"], ["HR 55 AN 4021", "22 ft SXL", "Sandeep Kumar", "5,106 km", "₹29.10"], ["KA 51 MN 6814", "32 ft MXL", "Vijay Raj", "7,214 km", "₹28.60"], ["GJ 01 KT 7730", "20 ft", "Irfan Sheikh", "4,832 km", "₹26.90"]]
  },
  Settlements: {
    eyebrow: "DRIVER ACCOUNTS", title: "Driver settlements", action: "+ New settlement",
    stats: [["Pending settlement", "₹1.84L", "Across 8 drivers"], ["Trip advances", "₹96,000", "11 open advances"], ["Settled this month", "₹7.2L", "42 settlements"]],
    columns: ["Driver", "Trip sheet", "Advance", "Expenses", "Status"],
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

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("fleetadmin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setWorking(true); setError("");
    const form = new FormData(e.currentTarget);
    const value = (name: string, fallback: string) => String(form.get(name) || fallback);
    try { await login(username, password); onLogin(); }
    catch { setError("Invalid username or password"); }
    finally { setWorking(false); }
  };
  return <main className="login-page"><section className="login-card">
    <div className="brand login-brand"><span className="brand-mark">p</span><span>phloz</span></div>
    <p className="eyebrow">FLEET MANAGEMENT SYSTEM</p><h1>Welcome back</h1><p>Sign in to manage consignments, trips, vehicles and billing.</p>
    <form onSubmit={submit}><label>Username<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></label><label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></label>
    {error && <div className="login-error">{error}</div>}<button className="primary" disabled={working}>{working ? "Signing in…" : "Sign in to workspace"}</button></form>
    <small>Secure access · Phloz Transport ERP</small>
  </section></main>;
}

const actionMeta: Record<string, { eyebrow: string; title: string; button: string }> = {
  lr: { eyebrow: "CONSIGNMENT BOOKING", title: "Generate digital LR", button: "Generate LR" },
  trip: { eyebrow: "DISPATCH PLANNING", title: "Create trip sheet", button: "Create trip sheet" },
  invoice: { eyebrow: "FREIGHT BILLING", title: "Generate customer invoice", button: "Generate invoice" },
  tracking: { eyebrow: "LIVE GPS", title: "Vehicle tracking", button: "Refresh location" },
  customer: { eyebrow: "CUSTOMER KYC", title: "Add customer", button: "Save customer" },
  quote: { eyebrow: "SALES", title: "Create quotation", button: "Save quotation" },
  vehicle: { eyebrow: "FLEET MASTER", title: "Add vehicle", button: "Save vehicle" },
  settlement: { eyebrow: "DRIVER ACCOUNTS", title: "Create settlement", button: "Save settlement" },
};

function ActionPanel({ type, onClose, onDone, onCreated }: { type: string; onClose: () => void; onDone: (message: string) => void; onCreated: () => void }) {
  const [complete, setComplete] = useState(false);
  const [vehicle, setVehicle] = useState("MH 04 JU 9182");
  const [reference, setReference] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [liveTrip, setLiveTrip] = useState<any>(null);
  const meta = actionMeta[type];
  useEffect(() => {
    if (type !== "tracking") return;
    fmsRequest<any>("trips/").then(payload => {
      const records = Array.isArray(payload) ? payload : payload.results || [];
      const trip = records[0] || null;
      setLiveTrip(trip);
      if (trip?.vehicle_number) setVehicle(trip.vehicle_number);
    }).catch(() => undefined);
  }, [type]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setWorking(true); setError("");
    try {
      if (type === "lr") {
        const number = "LR-" + Date.now().toString().slice(-6);
        await fmsRequest("lorry-receipts/", { method: "POST", body: JSON.stringify({ number, customer: 1, consignor: value("consignor", "Tata Consumer, Mumbai"), consignee: value("consignee", "D-Mart Warehouse, Pune"), origin: value("origin", "Mumbai"), destination: value("destination", "Pune"), material: value("material", "Packaged food products"), weight_kg: value("weight", "12400"), packages: Number(value("packages", "480")), eway_bill_number: value("eway_bill", "271234567890"), freight_amount: value("freight", "42800"), status: "booked" }) });
        setReference(number);
      } else if (type === "trip") {
        const number = "TRP-" + Date.now().toString().slice(-5);
        await fmsRequest("trips/", { method: "POST", body: JSON.stringify({ number, vehicle: 1, driver: 1, lorry_receipts: [1], origin: value("origin", "Mumbai"), destination: value("destination", "Pune"), planned_departure: new Date(Date.now() + 7200000).toISOString(), advance_amount: value("advance", "12000"), estimated_cost: value("estimated_cost", "31600"), status: "planned" }) });
        setReference(number);
      } else if (type === "customer") {
        const gstin = "27ABCDE" + Date.now().toString().slice(-4) + "F1Z5";
        await fmsRequest("customers/", { method: "POST", body: JSON.stringify({ name: value("customer_name", "New Transport Customer"), gstin, pan: value("pan", "ABCDE1234F"), phone: value("phone", "+91 98765 44001"), email: value("email", "operations@customer.example"), billing_address: value("address", "Mumbai, Maharashtra"), credit_limit: value("credit_limit", "500000"), kyc_status: value("kyc_status", "pending") }) });
        setReference(gstin);
      } else if (type === "quote") {
        const number = "QTN-" + Date.now().toString().slice(-5);
        const valid = new Date(Date.now() + 15 * 86400000).toISOString().slice(0, 10);
        await fmsRequest("quotes/", { method: "POST", body: JSON.stringify({ number, customer: 1, origin: value("origin", "Mumbai"), destination: value("destination", "Pune"), freight_amount: value("freight", "42800"), valid_until: valid, status: "sent" }) });
        setReference(number);
      } else if (type === "vehicle") {
        const registration = "MH 04 DEMO " + Date.now().toString().slice(-3);
        await fmsRequest("vehicles/", { method: "POST", body: JSON.stringify({ registration_number: value("registration", registration), vehicle_type: value("vehicle_type", "32 ft MXL"), capacity_kg: Number(value("capacity", "16000")), ownership: value("ownership", "owned"), status: "available", gps_device_id: "GPS-" + Date.now().toString().slice(-6) }) });
        setReference(registration);
      } else if (type === "settlement") {
        await fmsRequest("settlements/", { method: "POST", body: JSON.stringify({ trip: 1, driver: 1, advance_amount: value("advance", "12000"), approved_expenses: value("expenses", "18450"), net_payable: String(Number(value("expenses", "18450")) - Number(value("advance", "12000"))), status: "pending" }) });
        setReference("Driver settlement");
      } else if (type === "invoice") {
        const number = "INV-" + Date.now().toString().slice(-6);
        const due = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
        await fmsRequest("invoices/", { method: "POST", body: JSON.stringify({ number, customer: 1, trip: 1, freight_amount: value("freight", "38500"), additional_charges: value("additional", "1200"), tax_amount: value("tax", "3100"), total_amount: String(Number(value("freight", "38500")) + Number(value("additional", "1200")) + Number(value("tax", "3100"))), due_date: due, status: "issued" }) });
        setReference(number);
      }
      setComplete(true); onCreated();
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to save record"); }
    finally { setWorking(false); }
  };
  return <div className="modal-backdrop" onMouseDown={onClose}><section className={"action-panel " + (type === "tracking" ? "map-panel" : "")} onMouseDown={e => e.stopPropagation()}>
    <div className="panel-head"><div><p className="eyebrow">{meta.eyebrow}</p><h2>{meta.title}</h2></div><button className="panel-close" onClick={onClose}>×</button></div>
    {type === "tracking" ? <div className="tracking-layout">
      <div className="mock-map"><div className="map-road r1"/><div className="map-road r2"/><div className="map-road r3"/><span className="city mumbai">Mumbai</span><span className="city pune">Pune</span><span className="map-pin start">●</span><span className="map-pin vehicle">▰</span><span className="map-pin finish">●</span><div className="map-progress"/></div>
      <div className="vehicle-list"><label>Track vehicle<select value={vehicle} onChange={e => setVehicle(e.target.value)}><option>{liveTrip?.vehicle_number || "No assigned vehicle"}</option></select></label><div className="tracking-stat"><span>Status</span><strong>{liveTrip?.status?.replaceAll("_", " ") || "Loading live trip…"}</strong></div><div className="tracking-grid"><div><span>Speed</span><strong>{liveTrip?.tracking_events?.[0]?.speed_kph || 0} km/h</strong></div><div><span>Route</span><strong>{liveTrip ? liveTrip.origin + " → " + liveTrip.destination : "—"}</strong></div><div><span>Last update</span><strong>{liveTrip?.tracking_events?.[0]?.recorded_at ? new Date(liveTrip.tracking_events[0].recorded_at).toLocaleTimeString("en-IN") : "No GPS ping"}</strong></div><div><span>Trip</span><strong>{liveTrip?.number || "—"}</strong></div></div><div className="event-feed"><strong>Live trip events</strong>{(liveTrip?.tracking_events || []).slice(0, 4).map((event: any) => <p key={event.id}><i/>{event.description || event.event_type}<span>{new Date(event.recorded_at).toLocaleTimeString("en-IN")}</span></p>)}{liveTrip && !liveTrip.tracking_events?.length && <p>No GPS events received yet</p>}</div><button className="primary full-button" onClick={() => { setLiveTrip(null); fmsRequest<any>("trips/").then(payload => setLiveTrip((Array.isArray(payload) ? payload : payload.results || [])[0] || null)); onDone("GPS location refreshed"); }}>{meta.button}</button></div>
    </div> : complete ? <div className="success-state"><span>✓</span><h3>{reference} {type === "trip" ? "created" : "generated"}</h3><p>{type === "lr" ? "Digital LR is ready to print or share with the driver." : type === "trip" ? "Vehicle and driver are allocated. The trip is ready for dispatch." : "Invoice for ₹42,800 is ready to send to Tata Consumer Products."}</p><div className="document-preview"><b>phloz</b><strong>{type === "lr" ? "LORRY RECEIPT" : type === "trip" ? "TRIP SHEET" : "TAX INVOICE"}</strong><small>{type === "lr" ? "LR-240845 · Mumbai → Pune" : type === "trip" ? "TS-2845 · MH 04 JU 9182" : "INV-2026-0847 · ₹42,800"}</small></div><div className="success-actions"><button className="secondary" onClick={() => onDone("Document downloaded")}>⇩ Download PDF</button><button className="primary" onClick={() => onDone("Document shared on WhatsApp")}>Share via WhatsApp</button></div></div> :
      <form className="action-form" onSubmit={submit}>
        {type === "customer" && <div className="form-grid"><label>Customer name<input name="customer_name" defaultValue="New Transport Customer" required/></label><label>Email<input name="email" defaultValue="operations@customer.example"/></label><label>PAN<input name="pan" defaultValue="ABCDE1234F"/></label><label>Credit limit<input name="credit_limit" type="number" defaultValue="500000"/></label><label>Phone<input name="phone" defaultValue="+91 98765 44001"/></label><label>KYC status<select name="kyc_status"><option value="pending">Pending verification</option><option value="verified">Verified</option></select></label></div>}
        {type === "quote" && <div className="form-grid"><label>Customer<select><option>Tata Consumer Products</option></select></label><label>Origin<input name="origin" defaultValue="Mumbai"/></label><label>Destination<input name="destination" defaultValue="Pune"/></label><label>Freight<input name="freight" type="number" defaultValue="42800"/></label></div>}
        {type === "vehicle" && <div className="form-grid"><label>Registration<input name="registration" placeholder="MH 04 AB 1234" required/></label><label>Vehicle type<select name="vehicle_type"><option>32 ft MXL</option><option>22 ft SXL</option></select></label><label>Capacity (kg)<input name="capacity" type="number" defaultValue="16000"/></label><label>Ownership<select name="ownership"><option value="owned">Owned fleet</option><option value="vendor">Vendor</option></select></label></div>}
        {type === "settlement" && <><div className="form-grid"><label>Driver<select><option>Ramesh Yadav</option></select></label><label>Trip<select><option>TRP-2841</option></select></label><label>Advance<input name="advance" type="number" defaultValue="12000"/></label><label>Approved expenses<input name="expenses" type="number" defaultValue="18450"/></label></div><div className="invoice-total"><span>Net payable</span><strong>₹6,450</strong><small>Expenses less advance</small></div></>}
        {type === "lr" && <><div className="form-grid"><label>Customer<select><option>Tata Consumer Products</option><option>Asian Paints Ltd</option></select></label><label>Booking date<input type="date" defaultValue="2026-08-03"/></label><label>Consignor<input name="consignor" defaultValue="Tata Consumer, Mumbai"/></label><label>Consignee<input name="consignee" defaultValue="D-Mart Warehouse, Pune"/></label><label>Origin<input name="origin" defaultValue="Mumbai"/></label><label>Destination<input name="destination" defaultValue="Pune"/></label><label>Material<input name="material" defaultValue="Packaged food products"/></label><label>Weight (kg)<input name="weight" type="number" defaultValue="12400"/></label><label>Packages<input name="packages" type="number" defaultValue="480"/></label><label>E-way bill<input name="eway_bill" defaultValue="271234567890"/></label><label>Freight<input name="freight" type="number" defaultValue="42800"/></label></div><label>Special instructions<textarea defaultValue="Handle with care · Delivery before 5 PM"/></label></>}
        {type === "trip" && <><div className="form-grid"><label>Route<select><option>Mumbai → Pune</option><option>Delhi → Jaipur</option></select></label><label>Linked LR<select><option>LR-240845</option><option>LR-240831</option></select></label><label>Vehicle<select><option>MH 04 JU 9182 · Available</option><option>MH 12 PQ 4407 · Available</option></select></label><label>Driver<select><option>Ramesh Yadav · Available</option><option>Manoj Singh · Available</option></select></label><label>Trip advance<input name="advance" type="number" defaultValue="12000"/></label><label>Estimated cost<input name="estimated_cost" type="number" defaultValue="31600"/></label><label>Planned departure<input type="time" defaultValue="14:30"/></label></div><div className="cost-strip"><span>Estimated distance <b>149 km</b></span><span>Estimated cost <b>₹31,600</b></span><span>Expected margin <b>26.2%</b></span></div></>}
        {type === "invoice" && <><div className="form-grid"><label>Customer<select><option>Tata Consumer Products</option><option>Asian Paints Ltd</option></select></label><label>Completed trip<select><option>TRP-2836 · POD received</option><option>TRP-2831 · POD received</option></select></label><label>Freight amount<input name="freight" type="number" defaultValue="38500"/></label><label>Additional charges<input name="additional" type="number" defaultValue="1200"/></label><label>GST<input name="tax" type="number" defaultValue="3100"/></label><label>Payment terms<select><option>30 days</option><option>15 days</option><option>45 days</option></select></label></div><div className="invoice-total"><span>Invoice total</span><strong>₹42,800</strong><small>Freight + toll + GST</small></div></>}
        {error && <div className="form-error">{error}</div>}<div className="form-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" type="submit" disabled={working}>{working ? "Saving…" : meta.button}</button></div>
      </form>}
  </section></div>;
}


const liveModules: Record<string, { endpoint: string; map: (record: any) => string[] }> = {
  Customers: { endpoint: "customers/", map: r => [r.name, r.gstin, "₹" + Number(r.credit_limit).toLocaleString("en-IN"), r.email || "—", r.kyc_status] },
  Sales: { endpoint: "quotes/", map: r => [r.number, r.customer_name, r.origin + " → " + r.destination, "₹" + Number(r.freight_amount).toLocaleString("en-IN"), r.status] },
  Operations: { endpoint: "lorry-receipts/", map: r => [r.number, r.consignor + " → " + r.consignee, r.origin + " → " + r.destination, r.eway_bill_number || "—", r.status] },
  Fleet: { endpoint: "vehicles/", map: r => [r.registration_number, r.vehicle_type, r.ownership, Number(r.capacity_kg).toLocaleString("en-IN") + " kg", r.status] },
  Settlements: { endpoint: "settlements/", map: r => [r.driver_name, "Trip #" + r.trip, "₹" + Number(r.advance_amount).toLocaleString("en-IN"), "₹" + Number(r.approved_expenses).toLocaleString("en-IN"), r.status] },
  Invoices: { endpoint: "invoices/", map: r => [r.number, r.customer_name, r.due_date, "₹" + Number(r.total_amount).toLocaleString("en-IN"), r.status] },
};

function ModuleView({ name, onAction, reloadKey }: { name: string; onAction: (message: string) => void; reloadKey: number }) {
  const data = modules[name];
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<string[][]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  useEffect(() => {
    let active = true; setLoading(true); setLoadError("");
    fmsRequest<any>(liveModules[name].endpoint).then(payload => {
      if (!active) return;
      const records = Array.isArray(payload) ? payload : payload.results || [];
      setRows(records.map(liveModules[name].map));
    }).catch(error => { if (active) setLoadError(error instanceof Error ? error.message : "Unable to load records"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [name, reloadKey]);
  const visibleRows = rows.filter(row => row.join(" ").toLowerCase().includes(query.toLowerCase()));
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">{data.eyebrow}</p><h2>{data.title}</h2><p>Live records from the Phloz fleet database.</p></div><button className="primary module-action" onClick={() => onAction(data.action.replace("+ ", "") + " opened")}>{data.action}</button></div>
    <div className="module-stats"><div className="module-stat"><span>Total records</span><strong>{loading ? "—" : rows.length}</strong><small>Stored in the live database</small></div><div className="module-stat"><span>Data source</span><strong>Live</strong><small>EC2 fleet API</small></div><div className="module-stat"><span>Last synchronised</span><strong>Now</strong><small>Refreshes after every save</small></div></div>
    <section className="module-table-card"><div className="module-toolbar"><div><strong>All {name.toLowerCase()}</strong><span>{loading ? "Loading live records…" : visibleRows.length + " live records"}</span></div><div className="toolbar-actions"><input aria-label={"Search " + name} placeholder={"Search " + name.toLowerCase() + "..."} value={query} onChange={e => setQuery(e.target.value)} /><button onClick={() => onAction("Live data refreshed")}>↻ Refresh</button><button onClick={() => onAction("Report exported")}>⇩ Export</button></div></div>
      {loadError ? <div className="data-state error">{loadError}</div> : loading ? <div className="data-state">Loading records from EC2…</div> : visibleRows.length === 0 ? <div className="data-state">No records found. Use the action button to create one.</div> :
      <div className="table-wrap"><table><thead><tr>{data.columns.map(col => <th key={col}>{col}</th>)}<th>Action</th></tr></thead><tbody>{visibleRows.map((row, i) => <tr key={row[0] + i}>{row.map((cell, j) => <td key={j}>{j === 0 ? <strong>{cell}</strong> : j === row.length - 1 ? <span className={"status " + cell.toLowerCase().replaceAll(" ", "-")}>{cell}</span> : cell}</td>)}<td><button className="row-action" onClick={() => onAction(row[0] + " opened")}>View →</button></td></tr>)}</tbody></table></div>}
    </section>
  </div>;
}

export default function Home() {
  const [active, setActive] = useState("Overview");
  const [toast, setToast] = useState("");
  const [action, setAction] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const [dashboard, setDashboard] = useState<any>(null);
  useEffect(() => { setAuthenticated(Boolean(sessionStorage.getItem("fms_token"))); }, []);
  useEffect(() => {
    if (!authenticated) return;
    fmsRequest<any>("dashboard/").then(setDashboard).catch(() => undefined);
  }, [authenticated, dataVersion]);

  const show = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 2200);
  };

  if (!authenticated) return <LoginScreen onLogin={() => setAuthenticated(true)} />;

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
          <div className="top-actions"><button className="icon-button" aria-label="Search">⌕</button><button className="icon-button notification" aria-label="Notifications">♢</button><button className="primary" onClick={() => setAction("lr")}>＋ New LR booking</button></div>
        </header>

        {active === "Overview" ? <div className="page-grid"><section className="quick-actions"><div><p className="eyebrow">QUICK ACTIONS</p><h2>Run daily fleet operations</h2></div><button onClick={() => setAction("lr")}><span>▤</span><b>Generate LR</b><small>Book consignment</small></button><button onClick={() => setAction("trip")}><span>▦</span><b>Create trip sheet</b><small>Allocate vehicle & driver</small></button><button onClick={() => setAction("invoice")}><span>₹</span><b>Generate invoice</b><small>Bill a completed trip</small></button><button onClick={() => setAction("tracking")}><span>⌖</span><b>Track vehicles</b><small>View live GPS map</small></button></section>
          <section className="hero-card">
            <div><span className="live-pill"><i /> LIVE FLEET</span><h2>{dashboard?.vehicles_on_trip ?? "—"} of {dashboard?.vehicles ?? "—"} vehicles<br />are on the road</h2><p>{dashboard ? Math.round((dashboard.vehicles_on_trip / Math.max(dashboard.vehicles, 1)) * 100) : "—"}% fleet utilisation · {dashboard?.active_trips ?? "—"} active trips</p><button className="text-button" onClick={() => show("Live operations opened")}>View live operations <span>→</span></button></div>
            <div className="fleet-visual" aria-label="Fleet utilisation 78 percent"><div className="ring"><strong>{dashboard ? Math.round((dashboard.vehicles_on_trip / Math.max(dashboard.vehicles, 1)) * 100) : "—"}%</strong><span>utilised</span></div><div className="route-line"><span className="pin one" /><span className="truck">▰</span><span className="pin two" /></div></div>
          </section>

          <section className="metric-card"><div className="metric-top"><span className="metric-icon green">₹</span><span className="trend up">↗ 12.4%</span></div><p>Total invoiced</p><h3>₹{Number(dashboard?.invoice_total || 0).toLocaleString("en-IN")}</h3><small>{dashboard?.open_invoices ?? 0} open invoices</small></section>
          <section className="metric-card"><div className="metric-top"><span className="metric-icon blue">↗</span><span className="trend down">↘ 3.1%</span></div><p>Pending settlements</p><h3>₹{Number(dashboard?.pending_settlements || 0).toLocaleString("en-IN")}</h3><small>Driver and trip expenses</small></section>
          <section className="metric-card profit"><div className="metric-top"><span className="metric-icon violet">◎</span><span className="trend up">↗ 2.8%</span></div><p>Available vehicles</p><h3>{dashboard?.available_vehicles ?? "—"}</h3><small>Ready for allocation</small></section>

          <section className="workflow-card">
            <div className="section-heading"><div><p className="eyebrow">TODAY&apos;S WORKFLOW</p><h2>Keep operations moving</h2></div><button className="more">•••</button></div>
            <div className="workflow-list">{workflows.map((flow, i) => <button key={flow.name} className="workflow-row" onClick={() => show(`${flow.name} opened`)}><span className={`step ${flow.accent}`}>{String(i + 1).padStart(2,"0")}</span><span className="workflow-copy"><strong>{flow.name}</strong><small>{flow.detail}</small></span><span className={`flow-value ${flow.accent}`}>{flow.value}</span><span className="arrow">→</span></button>)}</div>
          </section>

          <section className="cash-card">
            <div className="section-heading"><div><p className="eyebrow">CASH POSITION</p><h2>Receivables</h2></div><button className="more">•••</button></div>
            <div className="donut-wrap"><div className="donut"><div><strong>₹{Number(dashboard?.invoice_total || 0).toLocaleString("en-IN")}</strong><span>invoiced</span></div></div></div>
            <div className="legend"><div><span><i className="dot current"/>Customers</span><strong>{dashboard?.customers ?? "—"}</strong></div><div><span><i className="dot overdue"/>Open invoices</span><strong>{dashboard?.open_invoices ?? "—"}</strong></div><div><span><i className="dot critical"/>KYC pending</span><strong>{dashboard?.kyc_pending ?? "—"}</strong></div></div>
            <button className="secondary" onClick={() => show("Invoice follow-ups opened")}>Review collections</button>
          </section>

          <section className="trips-card">
            <div className="section-heading"><div><p className="eyebrow">ACTIVE MOVEMENT</p><h2>Recent trips</h2></div><button className="link-button" onClick={() => show("All trips opened")}>View all trips →</button></div>
            <div className="table-wrap"><table><thead><tr><th>Trip & route</th><th>Vehicle</th><th>Driver</th><th>Status</th><th>ETA / POD</th><th>Revenue</th></tr></thead><tbody>{(dashboard?.recent_trips || []).map((t: any) => <tr key={t.id}><td><strong>{t.number}</strong><small>{t.origin} → {t.destination}</small></td><td>{t.vehicle_number}</td><td>{t.driver_name}</td><td><span className={`status ${t.status.toLowerCase().replaceAll("_","-")}`}>{t.status.replaceAll("_"," ")}</span></td><td>{t.planned_departure ? new Date(t.planned_departure).toLocaleString("en-IN") : "—"}</td><td><strong>₹{Number(t.estimated_cost || 0).toLocaleString("en-IN")}</strong></td></tr>)}</tbody></table></div>
          </section>
        </div> : active === "Modules" ? <FeatureHub onAction={show} /> : <ModuleView name={active as keyof typeof modules} reloadKey={dataVersion} onAction={(message) => {
          if (message.includes("Book LR")) setAction("lr");
          else if (message.includes("Generate invoice")) setAction("invoice");
          else if (message.includes("Add customer")) setAction("customer");
          else if (message.includes("New quotation")) setAction("quote");
          else if (message.includes("Add vehicle")) setAction("vehicle");
          else if (message.includes("New settlement")) setAction("settlement");
          else show(message);
        }} />}
      </section>
      {action && <ActionPanel type={action} onClose={() => setAction("")} onCreated={() => setDataVersion(v => v + 1)} onDone={(message) => { show(message); if (action !== "tracking") setAction(""); }} />}
      {toast && <div className="toast">✓ {toast}</div>}
    </main>
  );
}

