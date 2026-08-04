"use client";

import { useEffect, useState } from "react";
import { fmsRequest, login } from "./lib/fms-api";

const navGroups: { label: string; items: [string, string][] }[] = [
  { label: "WORKSPACE", items: [["Overview", "⌂"], ["Analytics", "◎"]] },
  { label: "TRANSPORT", items: [["Dispatch", "▦"], ["Orders", "◈"], ["Tracking", "⌖"], ["Operations", "▤"]] },
  { label: "COMMERCIAL", items: [["Customers", "◇"], ["Sales", "↗"], ["Rates", "⚖"], ["Invoices", "▥"]] },
  { label: "FLEET", items: [["Fleet", "▱"], ["Fleets", "▩"], ["Drivers", "♙"], ["Maintenance", "⚒"], ["Compliance", "▣"], ["Fuel", "⛽"], ["Issues", "⚠"]] },
  { label: "NETWORK", items: [["Vendors", "⌸"], ["Places", "⌂"], ["Zones", "◍"]] },
  { label: "FINANCE", items: [["Expenses", "▤"], ["Settlements", "₹"]] },
  { label: "PLATFORM", items: [["Modules", "⊞"]] },
];
const nav = navGroups.flatMap(group => group.items.map(item => item[0]));

const trips = [
  { id: "TRP-2841", route: "Mumbai → Pune", truck: "MH 04 JU 9182", driver: "Ramesh Yadav", status: "In transit", eta: "Today, 16:40", revenue: "₹42,800" },
  { id: "TRP-2839", route: "Delhi → Jaipur", truck: "HR 55 AN 4021", driver: "Sandeep Kumar", status: "Loading", eta: "Today, 19:15", revenue: "₹36,250" },
  { id: "TRP-2836", route: "Ahmedabad → Surat", truck: "GJ 01 KT 7730", driver: "Irfan Sheikh", status: "Delivered", eta: "POD received", revenue: "₹28,600" },
  { id: "TRP-2834", route: "Bengaluru → Chennai", truck: "KA 51 MN 6814", driver: "Vijay Raj", status: "Delayed", eta: "+ 2h 10m", revenue: "₹51,400" },
];

const liveWorkflows = (dashboard: any) => [
  { name: "Customer KYC", detail: "GSTIN, PAN & credit checks", value: `${dashboard?.kyc_pending ?? 0} pending`, accent: "amber", target: "Customers" },
  { name: "Consignment orders", detail: "Booked, allocated and moving", value: `${dashboard?.active_orders ?? 0} active`, accent: "blue", target: "Orders" },
  { name: "Compliance renewals", detail: "RC, insurance, permit, PUC & FASTag", value: `${dashboard?.documents_expiring ?? 0} due`, accent: "violet", target: "Compliance" },
  { name: "On-road expenses", detail: "Diesel, toll and driver bhatta (30 days)", value: `₹${Number((dashboard?.fuel_spend || 0) + (dashboard?.trip_expenses || 0)).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`, accent: "green", target: "Expenses" },
];

const modules: Record<string, { eyebrow: string; title: string; action: string; actionType?: string; blurb?: string; stats?: string[][]; columns: string[]; rows?: string[][] }> = {
  Vendors: {
    eyebrow: "PARTNER NETWORK", title: "Vendors & attached transporters", action: "+ Add vendor", actionType: "vendor",
    blurb: "Attached fleet owners, brokers, workshops and fuel partners with GST and payment terms.",
    columns: ["Vendor", "Type", "City", "GSTIN", "Status"],
  },
  Places: {
    eyebrow: "LOCATION MASTER", title: "Places & facilities", action: "+ Add place", actionType: "place",
    blurb: "Saved warehouses, plants, hubs, fuel stations and customer sites with geo-coordinates.",
    columns: ["Place", "Type", "City", "Pincode", "Status"],
  },
  Zones: {
    eyebrow: "GEOFENCING", title: "Service areas & zones", action: "+ Add zone", actionType: "zone",
    blurb: "Circular geofences used for allocation, rate cards and arrival detection.",
    columns: ["Zone", "Service area", "Centre", "Radius", "Type"],
  },
  Fleets: {
    eyebrow: "FLEET GROUPS", title: "Fleets", action: "+ Create fleet", actionType: "fleet",
    blurb: "Group owned and attached vehicles with their drivers under a service area.",
    columns: ["Fleet", "Service area", "Vehicles", "Drivers", "Status"],
  },
  Fuel: {
    eyebrow: "FUEL & MILEAGE", title: "Diesel entries", action: "+ Log fuel entry", actionType: "fuel",
    blurb: "Every fill-up with automatic mileage calculation against the previous odometer reading.",
    columns: ["Vehicle", "Date", "Litres", "Mileage", "Payment"],
  },
  Expenses: {
    eyebrow: "TRIP COSTING", title: "On-road expenses", action: "+ Add expense", actionType: "expense",
    blurb: "Toll, driver bhatta, loading, RTO and repair spend captured against trips and orders.",
    columns: ["Category", "Vehicle", "Date", "Amount", "Status"],
  },
  Issues: {
    eyebrow: "INCIDENTS", title: "Issues reported on road", action: "+ Report issue", actionType: "issue",
    blurb: "Breakdowns, tyre failures, detentions and check-post delays raised by drivers.",
    columns: ["Issue", "Vehicle", "Type", "Priority", "Status"],
  },
  Customers: {
    eyebrow: "CUSTOMER MASTER", title: "Customers & KYC", action: "+ Add customer", actionType: "customer",
    stats: [["Active customers", "128", "+8 this month"], ["KYC pending", "3", "Needs attention"], ["Credit exposure", "₹18.4L", "Across 11 customers"]],
    columns: ["Customer", "GSTIN", "Credit limit", "Email", "KYC status"],
    rows: [["Tata Consumer Products", "27AAACT2727Q1ZW", "₹8.0L", "₹2.4L", "Verified"], ["Asian Paints Ltd", "27AAACA3622K1ZV", "₹5.0L", "₹1.1L", "Verified"], ["V-Guard Industries", "32AAACV8098A1ZP", "₹3.5L", "₹84,000", "Pending"], ["Havells India", "07AAACH0351E1Z1", "₹6.0L", "₹0", "Verified"]]
  },
  Sales: {
    eyebrow: "SALES PIPELINE", title: "Quotes & contracts", action: "+ New quotation", actionType: "quote",
    stats: [["Open quotations", "16", "Worth ₹12.8L"], ["Won this month", "₹8.6L", "72% conversion"], ["Rate contracts", "24", "5 expiring soon"]],
    columns: ["Quote", "Customer", "Lane", "Freight", "Stage"],
    rows: [["QTN-1084", "Tata Consumer", "Mumbai → Pune", "₹42,800", "Negotiation"], ["QTN-1081", "Asian Paints", "Delhi → Jaipur", "₹36,250", "Accepted"], ["QTN-1079", "V-Guard", "Bengaluru → Chennai", "₹51,400", "Sent"], ["QTN-1076", "Havells India", "Gurugram → Lucknow", "₹64,200", "Draft"]]
  },
  Operations: {
    eyebrow: "TRANSPORT OPERATIONS", title: "LR, manifests & trips", action: "+ Book LR", actionType: "lr",
    stats: [["LRs today", "24", "18 dispatched"], ["Active trips", "32", "4 need attention"], ["POD pending", "7", "₹3.2L billable"]],
    columns: ["LR number", "Consignor → Consignee", "Route", "E-way bill", "Status"],
    rows: [["LR-240831", "Tata Consumer → D-Mart Pune", "TS-2841", "MH 04 JU 9182", "In transit"], ["LR-240829", "Asian Paints → Jaipur Depot", "TS-2839", "HR 55 AN 4021", "Loading"], ["LR-240826", "V-Guard → Chennai DC", "TS-2834", "KA 51 MN 6814", "Delayed"], ["LR-240822", "Havells → Lucknow Hub", "TS-2831", "UP 32 KL 1098", "Delivered"]]
  },
  Fleet: {
    eyebrow: "OWN FLEET", title: "Vehicles & trip costing", action: "+ Add vehicle", actionType: "vehicle",
    stats: [["Fleet size", "41", "32 on road"], ["Cost per km", "₹28.40", "↓ ₹1.20 vs July"], ["Maintenance due", "5", "2 critical"]],
    columns: ["Vehicle", "Type", "Ownership", "Capacity", "Status"],
    rows: [["MH 04 JU 9182", "32 ft MXL", "Ramesh Yadav", "6,842 km", "₹27.80"], ["HR 55 AN 4021", "22 ft SXL", "Sandeep Kumar", "5,106 km", "₹29.10"], ["KA 51 MN 6814", "32 ft MXL", "Vijay Raj", "7,214 km", "₹28.60"], ["GJ 01 KT 7730", "20 ft", "Irfan Sheikh", "4,832 km", "₹26.90"]]
  },
  Settlements: {
    eyebrow: "DRIVER ACCOUNTS", title: "Driver settlements", action: "+ New settlement", actionType: "settlement",
    stats: [["Pending settlement", "₹1.84L", "Across 8 drivers"], ["Trip advances", "₹96,000", "11 open advances"], ["Settled this month", "₹7.2L", "42 settlements"]],
    columns: ["Driver", "Trip sheet", "Advance", "Expenses", "Status"],
    rows: [["Ramesh Yadav", "TS-2841", "₹12,000", "₹18,450", "₹6,450 due"], ["Sandeep Kumar", "TS-2839", "₹10,000", "₹9,240", "₹760 recover"], ["Vijay Raj", "TS-2834", "₹15,000", "₹21,180", "₹6,180 due"], ["Irfan Sheikh", "TS-2836", "₹8,000", "₹11,620", "₹3,620 due"]]
  },
  Invoices: {
    eyebrow: "BILLING & COLLECTIONS", title: "Customer invoices", action: "+ Generate invoice", actionType: "invoice",
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
  maintenance: { eyebrow: "FLEET MAINTENANCE", title: "Create work order", button: "Save work order" },
};

type FormField = { name: string; label: string; type?: "text" | "number" | "date" | "select" | "textarea"; options?: [string, string][]; source?: string; value?: string; required?: boolean };
type FormSpec = { eyebrow: string; title: string; button: string; endpoint: string; fields: FormField[]; reference: (values: Record<string, string>, created: any) => string };

const sourceLabel: Record<string, (record: any) => string> = {
  "customers/": r => r.name, "vehicles/": r => r.registration_number, "drivers/": r => r.name,
  "places/": r => `${r.name} · ${r.city}`, "service-areas/": r => r.name, "service-rates/": r => r.name,
  "fleets/": r => r.name, "trips/": r => r.number, "vendors/": r => r.name, "orders/": r => r.number,
};

const today = () => new Date().toISOString().slice(0, 10);

// Declarative create forms for the Fleetbase inspired FleetOps records.
const recordForms: Record<string, FormSpec> = {
  vendor: {
    eyebrow: "PARTNER NETWORK", title: "Add vendor", button: "Save vendor", endpoint: "vendors/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Vendor name", required: true },
      { name: "code", label: "Vendor code", required: true },
      { name: "vendor_type", label: "Vendor type", type: "select", options: [["transporter", "Attached transporter"], ["broker", "Broker / commission agent"], ["workshop", "Workshop"], ["fuel", "Fuel station"], ["tyre", "Tyre vendor"], ["insurance", "Insurance"]] },
      { name: "contact_person", label: "Contact person" },
      { name: "phone", label: "Phone" },
      { name: "gstin", label: "GSTIN" },
      { name: "city", label: "City" },
      { name: "state", label: "State" },
      { name: "payment_terms_days", label: "Payment terms (days)", type: "number", value: "30" },
      { name: "tds_percent", label: "TDS %", type: "number", value: "2" },
    ],
  },
  place: {
    eyebrow: "LOCATION MASTER", title: "Add place", button: "Save place", endpoint: "places/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Place name", required: true },
      { name: "code", label: "Place code", required: true },
      { name: "place_type", label: "Type", type: "select", options: [["warehouse", "Warehouse"], ["hub", "Branch / hub"], ["customer", "Customer site"], ["plant", "Plant"], ["fuel_station", "Fuel station"], ["toll_plaza", "Toll plaza"], ["workshop", "Workshop"], ["parking", "Truck parking"], ["checkpost", "RTO check post"]] },
      { name: "service_area", label: "Service area", type: "select", source: "service-areas/" },
      { name: "city", label: "City", required: true },
      { name: "state", label: "State" },
      { name: "pincode", label: "Pincode" },
      { name: "latitude", label: "Latitude", type: "number" },
      { name: "longitude", label: "Longitude", type: "number" },
      { name: "loading_hours", label: "Loading hours", value: "09:00-21:00" },
      { name: "address", label: "Address", type: "textarea" },
    ],
  },
  zone: {
    eyebrow: "GEOFENCING", title: "Create zone", button: "Save zone", endpoint: "zones/",
    reference: values => values.name,
    fields: [
      { name: "service_area", label: "Service area", type: "select", source: "service-areas/", required: true },
      { name: "name", label: "Zone name", required: true },
      { name: "zone_type", label: "Zone type", type: "select", options: [["delivery", "Delivery zone"], ["pickup", "Pickup zone"], ["hub", "Hub zone"], ["restricted", "Restricted zone"]] },
      { name: "center_latitude", label: "Centre latitude", type: "number", value: "19.076", required: true },
      { name: "center_longitude", label: "Centre longitude", type: "number", value: "72.8777", required: true },
      { name: "radius_km", label: "Radius (km)", type: "number", value: "25" },
    ],
  },
  fleet: {
    eyebrow: "FLEET GROUPS", title: "Create fleet", button: "Save fleet", endpoint: "fleets/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Fleet name", required: true },
      { name: "code", label: "Fleet code", required: true },
      { name: "service_area", label: "Service area", type: "select", source: "service-areas/" },
      { name: "vendor", label: "Attached vendor", type: "select", source: "vendors/" },
      { name: "manager", label: "Fleet manager" },
    ],
  },
  fuel: {
    eyebrow: "FUEL & MILEAGE", title: "Log fuel entry", button: "Save fuel entry", endpoint: "fuel-entries/",
    reference: (values, created) => `${created?.volume_litres || values.volume_litres} L logged`,
    fields: [
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/", required: true },
      { name: "driver", label: "Driver", type: "select", source: "drivers/" },
      { name: "entry_date", label: "Date", type: "date", value: today() },
      { name: "odometer_km", label: "Odometer (km)", type: "number", required: true },
      { name: "volume_litres", label: "Litres", type: "number", value: "250", required: true },
      { name: "rate_per_litre", label: "Rate per litre (₹)", type: "number", value: "94.20" },
      { name: "payment_method", label: "Paid via", type: "select", options: [["fuel_card", "Fuel card"], ["fastag", "FASTag"], ["cash", "Cash"], ["upi", "UPI"], ["credit", "Station credit"]] },
      { name: "station_name", label: "Fuel station" },
      { name: "invoice_number", label: "Invoice number" },
    ],
  },
  expense: {
    eyebrow: "TRIP COSTING", title: "Add on-road expense", button: "Save expense", endpoint: "trip-expenses/",
    reference: values => `₹${values.amount} ${values.category.replaceAll("_", " ")}`,
    fields: [
      { name: "category", label: "Category", type: "select", options: [["toll", "Toll / FASTag"], ["diesel", "Diesel"], ["driver_allowance", "Driver bhatta"], ["loading", "Loading"], ["unloading", "Unloading"], ["rto_fine", "RTO fine"], ["police", "Police / checkpost"], ["parking", "Parking"], ["repair", "On-road repair"], ["permit", "Permit / border tax"], ["halting", "Halting charges"], ["other", "Other"]] },
      { name: "amount", label: "Amount (₹)", type: "number", required: true },
      { name: "expense_date", label: "Date", type: "date", value: today() },
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/" },
      { name: "driver", label: "Driver", type: "select", source: "drivers/" },
      { name: "trip", label: "Trip", type: "select", source: "trips/" },
      { name: "paid_by", label: "Paid by", type: "select", options: [["driver", "Driver"], ["company", "Company"], ["fastag", "FASTag wallet"], ["vendor", "Vendor"]] },
      { name: "receipt_number", label: "Receipt number" },
    ],
  },
  issue: {
    eyebrow: "INCIDENTS", title: "Report an issue", button: "Raise issue", endpoint: "issues/",
    reference: (_values, created) => created?.number || "Issue",
    fields: [
      { name: "issue_type", label: "Issue type", type: "select", options: [["breakdown", "Breakdown"], ["accident", "Accident"], ["tyre", "Tyre"], ["documents", "Documents"], ["delay", "Delay"], ["route", "Route deviation"], ["safety", "Safety"], ["fuel_theft", "Fuel pilferage"], ["other", "Other"]] },
      { name: "priority", label: "Priority", type: "select", options: [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["critical", "Critical"]] },
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/" },
      { name: "driver", label: "Reported by", type: "select", source: "drivers/" },
      { name: "location_text", label: "Location" },
      { name: "assigned_to", label: "Assigned to" },
      { name: "description", label: "What happened", type: "textarea" },
    ],
  },
  document: {
    eyebrow: "COMPLIANCE", title: "Add compliance document", button: "Save document", endpoint: "compliance-documents/",
    reference: values => values.number || "Document",
    fields: [
      { name: "document_type", label: "Document", type: "select", options: [["rc", "Registration certificate"], ["insurance", "Insurance"], ["fitness", "Fitness certificate"], ["permit_national", "National permit"], ["permit_state", "State permit"], ["puc", "PUC certificate"], ["road_tax", "Road tax"], ["fastag", "FASTag KYC"], ["gps_certificate", "GPS/VLT certificate"], ["licence", "Driving licence"], ["aadhaar", "Aadhaar"], ["police_verification", "Police verification"], ["medical", "Medical fitness"]] },
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/" },
      { name: "driver", label: "Driver", type: "select", source: "drivers/" },
      { name: "number", label: "Document number" },
      { name: "issuing_authority", label: "Issuing authority" },
      { name: "issue_date", label: "Issued on", type: "date" },
      { name: "expiry_date", label: "Valid until", type: "date", required: true },
      { name: "reminder_days", label: "Remind before (days)", type: "number", value: "30" },
    ],
  },
  order: {
    eyebrow: "FLEETOPS BOOKING", title: "Create consignment order", button: "Create order", endpoint: "orders/",
    reference: (_values, created) => created?.tracking_number || "Order",
    fields: [
      { name: "customer", label: "Customer", type: "select", source: "customers/", required: true },
      { name: "order_type", label: "Order type", type: "select", options: [["ftl", "Full truck load"], ["ptl", "Part truck load"], ["parcel", "Parcel"], ["rental", "Vehicle rental"], ["reverse", "Reverse pickup"]] },
      { name: "pickup", label: "Pickup place", type: "select", source: "places/", required: true },
      { name: "dropoff", label: "Drop place", type: "select", source: "places/", required: true },
      { name: "service_rate", label: "Rate card", type: "select", source: "service-rates/" },
      { name: "fleet", label: "Fleet", type: "select", source: "fleets/" },
      { name: "payload_description", label: "Material" },
      { name: "weight_kg", label: "Weight (kg)", type: "number", value: "12000" },
      { name: "packages", label: "Packages", type: "number", value: "1" },
      { name: "distance_km", label: "Distance (km)", type: "number" },
      { name: "declared_value", label: "Declared value (₹)", type: "number" },
      { name: "eway_bill_number", label: "E-way bill" },
      { name: "payment_mode", label: "Freight terms", type: "select", options: [["to_pay", "To pay"], ["paid", "Paid"], ["tbb", "To be billed"], ["cod", "Cash on delivery"]] },
      { name: "special_instructions", label: "Special instructions", type: "textarea" },
    ],
  },
};

function RecordForm({ spec, onClose, onSaved }: { spec: FormSpec; onClose: () => void; onSaved: (reference: string, created: any) => void }) {
  const [options, setOptions] = useState<Record<string, any[]>>({});
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  useEffect(() => {
    const sources = Array.from(new Set(spec.fields.map(field => field.source).filter(Boolean) as string[]));
    sources.forEach(source => {
      fmsRequest<any>(wholeSet(source)).then(payload => {
        setOptions(current => ({ ...current, [source]: asList(payload) }));
      }).catch(() => undefined);
    });
  }, [spec]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setWorking(true); setError("");
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const values: Record<string, string> = {};
    const payload: Record<string, unknown> = {};
    for (const field of spec.fields) {
      const raw = String(form.get(field.name) ?? "").trim();
      values[field.name] = raw;
      if (raw === "") continue;
      payload[field.name] = field.type === "number" || field.source ? Number(raw) : raw;
    }
    try {
      const created = await fmsRequest<any>(spec.endpoint, { method: "POST", body: JSON.stringify(payload) });
      onSaved(spec.reference(values, created), created);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to save record");
    } finally { setWorking(false); }
  };

  return <form className="action-form" onSubmit={submit}>
    <div className="form-grid">{spec.fields.filter(field => field.type !== "textarea").map(field => <label key={field.name}>{field.label}
      {field.source ? <select name={field.name} required={field.required} defaultValue="">
        <option value="">{(options[field.source] || []).length ? "Select…" : "Loading…"}</option>
        {(options[field.source] || []).map(record => <option key={record.id} value={record.id}>{sourceLabel[field.source!] ? sourceLabel[field.source!](record) : record.name}</option>)}
      </select>
      : field.type === "select" ? <select name={field.name} defaultValue={field.value || (field.options || [["", ""]])[0][0]}>{(field.options || []).map(option => <option key={option[0]} value={option[0]}>{option[1]}</option>)}</select>
      : <input name={field.name} type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"} step={field.type === "number" ? "any" : undefined} defaultValue={field.value || ""} required={field.required} />}
    </label>)}</div>
    {spec.fields.filter(field => field.type === "textarea").map(field => <label key={field.name}>{field.label}<textarea name={field.name} defaultValue={field.value || ""} /></label>)}
    {error && <div className="form-error">{error}</div>}
    <div className="form-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" type="submit" disabled={working}>{working ? "Saving…" : spec.button}</button></div>
  </form>;
}

function ActionPanel({ type, onClose, onDone, onCreated }: { type: string; onClose: () => void; onDone: (message: string) => void; onCreated: () => void }) {
  const [complete, setComplete] = useState(false);
  const [vehicle, setVehicle] = useState("MH 04 JU 9182");
  const [reference, setReference] = useState("");
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [liveTrip, setLiveTrip] = useState<any>(null);
  const spec = recordForms[type];
  const meta = actionMeta[type] || spec;
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
    const form = new FormData(e.currentTarget as HTMLFormElement);
    const value = (name: string, fallback: string) => String(form.get(name) || fallback);
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
      } else if (type === "maintenance") {
        const number = "WO-" + Date.now().toString().slice(-5);
        await fmsRequest("maintenance/", { method: "POST", body: JSON.stringify({ number, vehicle: 1, title: value("title", "Preventive service"), category: value("category", "preventive"), scheduled_date: value("scheduled_date", new Date().toISOString().slice(0,10)), odometer_km: Number(value("odometer", "6842")), estimated_cost: value("estimated_cost", "12500"), vendor: value("vendor", "Authorised Workshop"), status: "open" }) });
        setReference(number);
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
    </div> : complete ? (spec ? <div className="success-state"><span>✓</span><h3>{reference}</h3><p>Saved to the live fleet database. The module list has been refreshed.</p><div className="success-actions"><button className="secondary" onClick={onClose}>Close</button><button className="primary" onClick={() => { setComplete(false); setReference(""); }}>Add another</button></div></div> : <div className="success-state"><span>✓</span><h3>{reference} {type === "trip" ? "created" : "generated"}</h3><p>{type === "lr" ? "Digital LR is ready to print or share with the driver." : type === "trip" ? "Vehicle and driver are allocated. The trip is ready for dispatch." : "Invoice for ₹42,800 is ready to send to Tata Consumer Products."}</p><div className="document-preview"><b>phloz</b><strong>{type === "lr" ? "LORRY RECEIPT" : type === "trip" ? "TRIP SHEET" : "TAX INVOICE"}</strong><small>{type === "lr" ? "LR-240845 · Mumbai → Pune" : type === "trip" ? "TS-2845 · MH 04 JU 9182" : "INV-2026-0847 · ₹42,800"}</small></div><div className="success-actions"><button className="secondary" onClick={() => onDone("Document downloaded")}>⇩ Download PDF</button><button className="primary" onClick={() => onDone("Document shared on WhatsApp")}>Share via WhatsApp</button></div></div>) : spec ? <RecordForm spec={spec} onClose={onClose} onSaved={(newReference) => { setReference(newReference); setComplete(true); onCreated(); }} /> :
      <form className="action-form" onSubmit={submit}>
        {type === "customer" && <div className="form-grid"><label>Customer name<input name="customer_name" defaultValue="New Transport Customer" required/></label><label>Email<input name="email" defaultValue="operations@customer.example"/></label><label>PAN<input name="pan" defaultValue="ABCDE1234F"/></label><label>Credit limit<input name="credit_limit" type="number" defaultValue="500000"/></label><label>Phone<input name="phone" defaultValue="+91 98765 44001"/></label><label>KYC status<select name="kyc_status"><option value="pending">Pending verification</option><option value="verified">Verified</option></select></label></div>}
        {type === "quote" && <div className="form-grid"><label>Customer<select><option>Tata Consumer Products</option></select></label><label>Origin<input name="origin" defaultValue="Mumbai"/></label><label>Destination<input name="destination" defaultValue="Pune"/></label><label>Freight<input name="freight" type="number" defaultValue="42800"/></label></div>}
        {type === "vehicle" && <div className="form-grid"><label>Registration<input name="registration" placeholder="MH 04 AB 1234" required/></label><label>Vehicle type<select name="vehicle_type"><option>32 ft MXL</option><option>22 ft SXL</option></select></label><label>Capacity (kg)<input name="capacity" type="number" defaultValue="16000"/></label><label>Ownership<select name="ownership"><option value="owned">Owned fleet</option><option value="vendor">Vendor</option></select></label></div>}
        {type === "settlement" && <><div className="form-grid"><label>Driver<select><option>Ramesh Yadav</option></select></label><label>Trip<select><option>TRP-2841</option></select></label><label>Advance<input name="advance" type="number" defaultValue="12000"/></label><label>Approved expenses<input name="expenses" type="number" defaultValue="18450"/></label></div><div className="invoice-total"><span>Net payable</span><strong>₹6,450</strong><small>Expenses less advance</small></div></>}
        {type === "maintenance" && <div className="form-grid"><label>Work description<input name="title" defaultValue="Preventive service"/></label><label>Category<select name="category"><option value="preventive">Preventive</option><option value="breakdown">Breakdown</option></select></label><label>Scheduled date<input name="scheduled_date" type="date" defaultValue={new Date().toISOString().slice(0,10)}/></label><label>Odometer (km)<input name="odometer" type="number" defaultValue="6842"/></label><label>Estimated cost<input name="estimated_cost" type="number" defaultValue="12500"/></label><label>Workshop/vendor<input name="vendor" defaultValue="Authorised Workshop"/></label></div>}
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
  Vendors: { endpoint: "vendors/", map: r => [r.name, r.vendor_type, r.city || "—", r.gstin || "—", r.status] },
  Places: { endpoint: "places/", map: r => [r.name, r.place_type, r.city, r.pincode || "—", r.status] },
  Zones: { endpoint: "zones/", map: r => [r.name, r.service_area_name, Number(r.center_latitude).toFixed(3) + ", " + Number(r.center_longitude).toFixed(3), r.radius_km + " km", r.zone_type] },
  Fleets: { endpoint: "fleets/", map: r => [r.name, r.service_area_name || "—", String(r.vehicle_count), String(r.driver_count), r.status] },
  Fuel: { endpoint: "fuel-entries/", map: r => [r.vehicle_number, r.entry_date, Number(r.volume_litres).toFixed(2) + " L", Number(r.mileage_kmpl) ? Number(r.mileage_kmpl).toFixed(2) + " km/l" : "—", r.payment_method] },
  Expenses: { endpoint: "trip-expenses/", map: r => [r.category.replaceAll("_", " "), r.vehicle_number || "—", r.expense_date, "₹" + Number(r.amount).toLocaleString("en-IN"), r.status] },
  Issues: { endpoint: "issues/", map: r => [r.number, r.vehicle_number || "—", r.issue_type, r.priority, r.status] },
};

function ModuleView({ name, onAction, reloadKey, openAction }: { name: string; onAction: (message: string) => void; reloadKey: number; openAction: (type: string) => void }) {
  const data = modules[name];
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<string[][]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selectedRow, setSelectedRow] = useState<string[] | null>(null);
  useEffect(() => {
    let active = true; setLoading(true); setLoadError("");
    fmsRequest<any>(wholeSet(liveModules[name].endpoint)).then(payload => {
      if (!active) return;
      const records = asList(payload);
      setRows(records.map(liveModules[name].map));
      setTotal(asCount(payload, records));
    }).catch(error => { if (active) setLoadError(error instanceof Error ? error.message : "Unable to load records"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [name, reloadKey]);
  const visibleRows = rows.filter(row => row.join(" ").toLowerCase().includes(query.toLowerCase()));
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">{data.eyebrow}</p><h2>{data.title}</h2><p>{data.blurb || "Live records from the Phloz fleet database."}</p></div><button className="primary module-action" onClick={() => data.actionType ? openAction(data.actionType) : onAction(data.action.replace("+ ", "") + " opened")}>{data.action}</button></div>
    <div className="module-stats"><div className="module-stat"><span>Total records</span><strong>{loading ? "—" : total}</strong><small>{!loading && total > rows.length ? `Showing the first ${rows.length}` : "Stored in the live database"}</small></div><div className="module-stat"><span>Data source</span><strong>Live</strong><small>EC2 fleet API</small></div><div className="module-stat"><span>Last synchronised</span><strong>Now</strong><small>Refreshes after every save</small></div></div>
    <section className="module-table-card"><div className="module-toolbar"><div><strong>All {name.toLowerCase()}</strong><span>{loading ? "Loading live records…" : visibleRows.length + " live records"}</span></div><div className="toolbar-actions"><input aria-label={"Search " + name} placeholder={"Search " + name.toLowerCase() + "..."} value={query} onChange={e => setQuery(e.target.value)} /><button onClick={() => onAction("Live data refreshed")}>↻ Refresh</button><button onClick={() => onAction("Report exported")}>⇩ Export</button></div></div>
      {loadError ? <div className="data-state error">{loadError}</div> : loading ? <div className="data-state">Loading records from EC2…</div> : visibleRows.length === 0 ? <div className="data-state">No records found. Use the action button to create one.</div> :
      <div className="table-wrap"><table><thead><tr>{data.columns.map(col => <th key={col}>{col}</th>)}<th>Action</th></tr></thead><tbody>{visibleRows.map((row, i) => <tr key={row[0] + i}>{row.map((cell, j) => <td key={j}>{j === 0 ? <strong>{cell}</strong> : j === row.length - 1 ? <span className={"status " + cell.toLowerCase().replaceAll(" ", "-")}>{cell}</span> : cell}</td>)}<td><button className="row-action" onClick={() => setSelectedRow(row)}>View →</button></td></tr>)}</tbody></table></div>}
    </section>
    {selectedRow && <div className="record-backdrop" onMouseDown={() => setSelectedRow(null)}><aside className="record-drawer" onMouseDown={e => e.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">{data.eyebrow}</p><h2>{selectedRow[0]}</h2><span className={"status " + selectedRow[selectedRow.length - 1].toLowerCase().replaceAll(" ", "-")}>{selectedRow[selectedRow.length - 1]}</span></div><button className="panel-close" onClick={() => setSelectedRow(null)}>×</button></div>
      <div className="record-fields">{data.columns.map((column, index) => <div className="record-field" key={column}><span>{column}</span><strong>{selectedRow[index] || "—"}</strong></div>)}</div>
      <div className="record-timeline"><p className="eyebrow">RECORD ACTIVITY</p><div><i/><span><strong>Record loaded</strong><small>Live data from EC2 fleet API</small></span><time>Now</time></div><div><i/><span><strong>Last synchronised</strong><small>Changes are persisted automatically</small></span><time>Live</time></div></div>
      <div className="record-actions"><button className="secondary" onClick={() => setSelectedRow(null)}>Close</button><button className="primary" onClick={() => onAction("Edit workflow opened for " + selectedRow[0])}>Edit record</button></div>
    </aside></div>}
  </div>;
}


const fleetOpsPages = ["Dispatch", "Tracking", "Drivers", "Maintenance", "Analytics"];

function FleetOpsView({ name, onAction, reloadKey, openAction }: { name: string; onAction: (message: string) => void; reloadKey: number; openAction: (type: string) => void }) {
  const [records, setRecords] = useState<any[]>([]);
  const [dashboard, setDashboard] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const endpoint = name === "Dispatch" || name === "Tracking" ? "trips/" : name === "Drivers" ? "drivers/" : name === "Maintenance" ? "maintenance/" : "analytics/fleet/";
  const load = () => {
    setLoading(true);
    fmsRequest<any>(name === "Analytics" ? endpoint : wholeSet(endpoint)).then(payload => {
      if (name === "Analytics") setDashboard(payload);
      else setRecords(asList(payload));
    }).finally(() => setLoading(false));
  };
  useEffect(load, [name, reloadKey]);
  const tripAction = async (trip: any, action: string) => {
    await fmsRequest("trips/" + trip.id + "/" + action + "/", { method: "POST" });
    onAction(trip.number + " " + action + "ed"); load();
  };
  if (name === "Dispatch") return <div className="module-page"><div className="module-title"><div><p className="eyebrow">FLEET-OPS DISPATCH</p><h2>Dispatch command board</h2><p>Plan, assign and progress trips through a visual workflow.</p></div><button className="primary module-action" onClick={() => openAction("trip")}>＋ Create trip</button></div>
    <div className="dispatch-board">{["planned","dispatched","in_transit","closed"].map(status => <section className="dispatch-column" key={status}><header><strong>{status.replaceAll("_"," ")}</strong><span>{records.filter(r => r.status === status).length}</span></header>{records.filter(r => r.status === status).map(trip => <article className="dispatch-card" key={trip.id}><b>{trip.number}</b><p>{trip.origin} → {trip.destination}</p><small>{trip.vehicle_number} · {trip.driver_name}</small><div>{status === "planned" && <button onClick={() => tripAction(trip,"dispatch")}>Dispatch</button>}{status !== "closed" && status !== "planned" && <button onClick={() => tripAction(trip,"close")}>Close trip</button>}</div></article>)}{!loading && !records.some(r => r.status === status) && <div className="empty-column">No trips</div>}</section>)}</div></div>;
  if (name === "Tracking") { const trip=records[0]; const event=trip?.tracking_events?.[0]; return <div className="module-page"><div className="module-title"><div><p className="eyebrow">LIVE FLEET MAP</p><h2>Track fleet operations</h2><p>GPS positions, routes, geofences and automated trip events.</p></div><button className="primary module-action" onClick={load}>↻ Refresh GPS</button></div><div className="full-map-layout"><div className="operations-map"><div className="map-road r1"/><div className="map-road r2"/><div className="map-road r3"/><span className="city mumbai">Mumbai</span><span className="city pune">Pune</span><span className="map-pin start">●</span><span className="map-pin vehicle">▰</span><span className="map-pin finish">●</span><div className="map-progress"/></div><aside className="map-details"><p className="eyebrow">SELECTED TRIP</p><h3>{trip?.number || "No active trip"}</h3><p>{trip ? trip.origin + " → " + trip.destination : "Create a trip to begin tracking"}</p><div className="tracking-grid"><div><span>Vehicle</span><strong>{trip?.vehicle_number || "—"}</strong></div><div><span>Driver</span><strong>{trip?.driver_name || "—"}</strong></div><div><span>Speed</span><strong>{event?.speed_kph || 0} km/h</strong></div><div><span>Status</span><strong>{trip?.status?.replaceAll("_"," ") || "—"}</strong></div></div><div className="event-feed"><strong>Latest events</strong>{(trip?.tracking_events || []).map((e:any)=><p key={e.id}><i/>{e.description || e.event_type}<span>{new Date(e.recorded_at).toLocaleTimeString("en-IN")}</span></p>)}</div></aside></div></div>; }
  if (name === "Drivers") return <div className="module-page"><div className="module-title"><div><p className="eyebrow">DRIVER OPERATIONS</p><h2>Drivers & availability</h2><p>Licences, shifts, current status and last known location.</p></div></div><section className="module-table-card"><div className="table-wrap"><table><thead><tr><th>Driver</th><th>Phone</th><th>Licence</th><th>Expiry</th><th>Status</th></tr></thead><tbody>{records.map(r=><tr key={r.id}><td><strong>{r.name}</strong></td><td>{r.phone}</td><td>{r.licence_number}</td><td>{r.licence_expiry || "—"}</td><td><span className={"status "+r.status}>{r.status}</span></td></tr>)}</tbody></table></div></section></div>;
  if (name === "Maintenance") return <div className="module-page"><div className="module-title"><div><p className="eyebrow">FLEET MAINTENANCE</p><h2>Work orders & schedules</h2><p>Preventive servicing, breakdowns and vehicle downtime.</p></div><button className="primary module-action" onClick={() => openAction("maintenance")}>＋ New work order</button></div><section className="module-table-card"><div className="table-wrap"><table><thead><tr><th>Work order</th><th>Vehicle</th><th>Work</th><th>Scheduled</th><th>Cost</th><th>Status</th></tr></thead><tbody>{records.map(r=><tr key={r.id}><td><strong>{r.number}</strong></td><td>{r.vehicle_number}</td><td>{r.title}</td><td>{r.scheduled_date}</td><td>₹{Number(r.estimated_cost).toLocaleString("en-IN")}</td><td><span className={"status "+r.status}>{r.status}</span></td></tr>)}</tbody></table></div></section></div>;
  const cards: [string, any, string][] = [
    ["Fleet utilisation", (dashboard?.utilisation_percent ?? 0) + "%", `${dashboard?.vehicles_on_trip ?? 0} of ${dashboard?.fleet_size ?? 0} trucks on road`],
    ["Cost per km", "₹" + Number(dashboard?.cost_per_km || 0).toFixed(2), "Diesel plus on-road expenses"],
    ["Average mileage", Number(dashboard?.average_mileage_kmpl || 0).toFixed(2) + " km/l", "Across all fuel entries"],
    ["Orders completed", `${dashboard?.orders_completed ?? 0}/${dashboard?.orders ?? 0}`, `${dashboard?.on_time_percent ?? 0}% delivered on time`],
    ["Order revenue", rupees(dashboard?.order_revenue), "Freight booked in the period"],
    ["Diesel spend", rupees(dashboard?.fuel_spend), "Fuel card, FASTag and cash fills"],
    ["Trip expenses", rupees(dashboard?.trip_expenses), "Toll, bhatta, loading and repairs"],
    ["Open issues", dashboard?.open_issues ?? 0, "Breakdowns and detentions"],
    ["Compliance alerts", dashboard?.documents_expiring ?? 0, `${dashboard?.maintenance_due ?? 0} services also due`],
  ];
  const split = dashboard?.expense_split || [];
  const peak = Math.max(1, ...split.map((row: any) => Number(row.total || 0)));
  return <div className="module-page"><div className="module-title"><div><p className="eyebrow">OPERATIONS ANALYTICS</p><h2>Fleet performance</h2><p>Cost per km, mileage, utilisation and compliance exposure from the last 30 days.</p></div><button className="primary module-action" onClick={load}>↻ Refresh</button></div>
    <div className="analytics-grid">{cards.map(card => <div className="analytics-card" key={card[0]}><span>{card[0]}</span><strong>{loading ? "—" : card[1]}</strong><small>{card[2]}</small></div>)}</div>
    <section className="module-table-card expense-split"><div className="module-toolbar"><div><strong>Where the money goes</strong><span>On-road expense split for the period</span></div></div>
      {split.length ? <div className="split-bars">{split.map((row: any) => <div className="split-row" key={row.category}><span>{String(row.category).replaceAll("_", " ")}</span><i style={{ width: `${Math.max(4, (Number(row.total) / peak) * 100)}%` }} /><strong>{rupees(row.total)}</strong></div>)}</div>
        : <div className="data-state">No expenses recorded yet in this period.</div>}
    </section>
  </div>;
}

const asList = (payload: any): any[] => (Array.isArray(payload) ? payload : payload?.results || []);
// DRF paginates at 50 by default, so `results.length` is a page size, not a total.
const asCount = (payload: any, records: any[]): number => (typeof payload?.count === "number" ? payload.count : records.length);
// Board and watchlist screens render a whole working set, so they ask for a larger page.
const wholeSet = (endpoint: string) => endpoint + (endpoint.includes("?") ? "&" : "?") + "page_size=500";
const rupees = (value: any) => "₹" + Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const orderColumns: [string, string][] = [["created", "Booked"], ["assigned", "Allocated"], ["dispatched", "Dispatched"], ["in_transit", "In transit"], ["completed", "Delivered"]];

function OrdersView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: (message: string) => void; openAction: (type: string) => void }) {
  const [orders, setOrders] = useState<any[]>([]);
  const [drivers, setDrivers] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [orderTotal, setOrderTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [driver, setDriver] = useState("");
  const [vehicle, setVehicle] = useState("");

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("orders/")).then(payload => {
      const records = asList(payload);
      setOrders(records);
      setOrderTotal(asCount(payload, records));
    }).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);
  useEffect(() => {
    fmsRequest<any>(wholeSet("drivers/")).then(payload => setDrivers(asList(payload))).catch(() => undefined);
    fmsRequest<any>(wholeSet("vehicles/")).then(payload => setVehicles(asList(payload))).catch(() => undefined);
  }, []);

  const run = async (order: any, path: string, body?: Record<string, unknown>) => {
    setBusy(true);
    try {
      const updated = await fmsRequest<any>(`orders/${order.id}/${path}/`, { method: "POST", body: JSON.stringify(body || {}) });
      onAction(`${order.number} ${path.replace("_", " ")}`);
      setSelected(updated?.id ? updated : null);
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 90) : "Action failed");
    } finally { setBusy(false); }
  };

  const totalValue = orders.reduce((sum, order) => sum + Number(order.total_amount || 0), 0);
  const active = orders.filter(order => !["completed", "cancelled"].includes(order.status));

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">FLEETOPS ORDERS</p><h2>Consignment orders</h2><p>Booking to ePOD, with waypoints, live activity feed and consignee tracking numbers.</p></div><button className="primary module-action" onClick={() => openAction("order")}>＋ New order</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Total orders</span><strong>{loading ? "—" : orderTotal}</strong><small>Across all statuses</small></div>
      <div className="module-stat"><span>Active now</span><strong>{loading ? "—" : active.length}</strong><small>Booked, allocated or moving</small></div>
      <div className="module-stat"><span>Order value</span><strong>{rupees(totalValue)}</strong><small>Freight incl. GST</small></div>
    </div>
    <div className="dispatch-board">{orderColumns.map(([status, label]) => {
      const bucket = orders.filter(order => order.status === status);
      return <section className="dispatch-column" key={status}>
        <header><strong>{label}</strong><span>{bucket.length}</span></header>
        {bucket.map(order => <article className="dispatch-card" key={order.id}>
          <b>{order.number}</b>
          <p>{order.pickup_city} → {order.dropoff_city}</p>
          <small>{order.customer_name} · {rupees(order.total_amount)}</small>
          <small className="tracking-code">{order.tracking_number}</small>
          <div>
            <button onClick={() => { setSelected(order); setDriver(order.driver || ""); setVehicle(order.vehicle || ""); }}>Open</button>
            {status === "created" && <button disabled={busy} onClick={() => { setSelected(order); setDriver(""); setVehicle(""); }}>Allocate</button>}
            {status === "assigned" && <button disabled={busy} onClick={() => run(order, "dispatch")}>Dispatch</button>}
            {["dispatched", "in_transit"].includes(status) && <button disabled={busy} onClick={() => run(order, "complete", { receiver_name: "Consignee", proof_type: "signature" })}>Deliver</button>}
          </div>
        </article>)}
        {!loading && bucket.length === 0 && <div className="empty-column">No orders</div>}
      </section>;
    })}</div>

    {selected && <div className="record-backdrop" onMouseDown={() => setSelected(null)}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">CONSIGNMENT {selected.tracking_number}</p><h2>{selected.number}</h2><span className={"status " + String(selected.status).replaceAll("_", "-")}>{String(selected.status).replaceAll("_", " ")}</span></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>
      <div className="record-fields">
        <div className="record-field"><span>Customer</span><strong>{selected.customer_name}</strong></div>
        <div className="record-field"><span>Lane</span><strong>{selected.pickup_name} → {selected.dropoff_name}</strong></div>
        <div className="record-field"><span>Material</span><strong>{selected.payload_description || "—"}</strong></div>
        <div className="record-field"><span>Weight</span><strong>{Number(selected.weight_kg).toLocaleString("en-IN")} kg · {selected.packages} pkg</strong></div>
        <div className="record-field"><span>Distance</span><strong>{Number(selected.distance_km).toFixed(1)} km</strong></div>
        <div className="record-field"><span>Freight + GST</span><strong>{rupees(selected.freight_amount)} + {rupees(selected.tax_amount)}</strong></div>
        <div className="record-field"><span>Total</span><strong>{rupees(selected.total_amount)}</strong></div>
        <div className="record-field"><span>E-way bill</span><strong>{selected.eway_bill_number || "—"}</strong></div>
      </div>
      {selected.status === "created" || !selected.driver ? <div className="allocate-box">
        <p className="eyebrow">ALLOCATE VEHICLE & DRIVER</p>
        <div className="allocate-grid">
          <label>Driver<select value={driver} onChange={event => setDriver(event.target.value)}><option value="">Select driver</option>{drivers.map(record => <option key={record.id} value={record.id}>{record.name} · {record.status}</option>)}</select></label>
          <label>Vehicle<select value={vehicle} onChange={event => setVehicle(event.target.value)}><option value="">Select vehicle</option>{vehicles.map(record => <option key={record.id} value={record.id}>{record.registration_number} · {record.status}</option>)}</select></label>
        </div>
        <button className="primary full-button" disabled={busy || !driver || !vehicle} onClick={() => run(selected, "assign", { driver: Number(driver), vehicle: Number(vehicle) })}>Assign to order</button>
      </div> : <div className="allocate-box"><p className="eyebrow">ALLOCATION</p><div className="tracking-grid"><div><span>Vehicle</span><strong>{selected.vehicle_number || "—"}</strong></div><div><span>Driver</span><strong>{selected.driver_name || "—"}</strong></div></div></div>}
      <div className="record-timeline"><p className="eyebrow">WAYPOINTS</p>
        {(selected.waypoints || []).map((point: any) => <div key={point.id}><i /><span><strong>{point.sequence}. {point.place_name}</strong><small>{point.waypoint_type} · {point.city}</small></span><time>{point.status}</time></div>)}
        {!(selected.waypoints || []).length && <div><i /><span><strong>Direct movement</strong><small>Pickup to drop without intermediate stops</small></span><time>—</time></div>}
      </div>
      <div className="record-timeline"><p className="eyebrow">TRACKING ACTIVITY</p>
        {(selected.activities || []).map((activity: any) => <div key={activity.id}><i /><span><strong>{activity.code.replaceAll("_", " ")}</strong><small>{activity.details || activity.status}{activity.city ? ` · ${activity.city}` : ""}</small></span><time>{new Date(activity.recorded_at).toLocaleString("en-IN")}</time></div>)}
      </div>
      <div className="record-actions">
        <button className="secondary" disabled={busy || selected.status === "cancelled"} onClick={() => run(selected, "cancel", { reason: "Cancelled from dispatch desk" })}>Cancel order</button>
        <button className="primary" disabled={busy || !selected.service_rate} onClick={() => run(selected, "reprice")}>Reprice</button>
      </div>
    </aside></div>}
  </div>;
}

function RatesView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: (message: string) => void; openAction: (type: string) => void }) {
  const [rates, setRates] = useState<any[]>([]);
  const [quote, setQuote] = useState<any>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { fmsRequest<any>(wholeSet("service-rates/")).then(payload => setRates(asList(payload))).catch(() => undefined); }, [reloadKey]);

  const estimate = async (event: React.FormEvent) => {
    event.preventDefault();
    setWorking(true); setError(""); setQuote(null);
    const form = new FormData(event.currentTarget as HTMLFormElement);
    try {
      const payload = await fmsRequest<any>("service-rates/quote/", { method: "POST", body: JSON.stringify({
        service_rate: Number(form.get("service_rate")), origin: String(form.get("origin") || ""), destination: String(form.get("destination") || ""),
        distance_km: Number(form.get("distance_km") || 0), weight_kg: Number(form.get("weight_kg") || 0),
        halt_days: Number(form.get("halt_days") || 0), other_charges: Number(form.get("other_charges") || 0),
        save_quote: form.get("save_quote") === "on" }) });
      setQuote(payload.breakdown);
      onAction(payload.quote ? `Quote ${payload.quote.number} saved` : "Freight estimated");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to price this lane");
    } finally { setWorking(false); }
  };

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">RATE MANAGEMENT</p><h2>Rate cards & freight estimator</h2><p>Per km, per ton-km, per kg and fixed lane pricing with GST, RCM and fuel surcharge.</p></div><button className="primary module-action" onClick={() => openAction("quote")}>＋ New quotation</button></div>
    <div className="rate-layout">
      <section className="module-table-card">
        <div className="module-toolbar"><div><strong>Active rate cards</strong><span>{rates.length} cards</span></div></div>
        <div className="table-wrap"><table><thead><tr><th>Rate card</th><th>Basis</th><th>Base</th><th>Per km</th><th>GST</th><th>Status</th></tr></thead>
          <tbody>{rates.map(rate => <tr key={rate.id}>
            <td><strong>{rate.name}</strong><small>{rate.service_area_name || "All areas"} · {rate.vehicle_type || "Any vehicle"}</small></td>
            <td>{String(rate.rate_type).replaceAll("_", " ")}</td>
            <td>{rupees(rate.base_charge)}</td>
            <td>{rupees(rate.per_km_rate)}</td>
            <td>{rate.reverse_charge ? "RCM" : `${Number(rate.gst_percent)}%`}</td>
            <td><span className={"status " + rate.status}>{rate.status}</span></td>
          </tr>)}</tbody></table></div>
      </section>
      <aside className="quote-card">
        <p className="eyebrow">FREIGHT ESTIMATOR</p><h3>Price a lane</h3>
        <form className="action-form quote-form" onSubmit={estimate}>
          <label>Rate card<select name="service_rate" required>{rates.map(rate => <option key={rate.id} value={rate.id}>{rate.name}</option>)}</select></label>
          <div className="form-grid">
            <label>Origin<input name="origin" defaultValue="Bhiwandi" /></label>
            <label>Destination<input name="destination" defaultValue="Chakan" /></label>
            <label>Distance (km)<input name="distance_km" type="number" step="any" defaultValue="150" /></label>
            <label>Weight (kg)<input name="weight_kg" type="number" step="any" defaultValue="12400" /></label>
            <label>Halting days<input name="halt_days" type="number" step="any" defaultValue="0" /></label>
            <label>Other charges<input name="other_charges" type="number" step="any" defaultValue="0" /></label>
          </div>
          <label className="checkbox-row"><input type="checkbox" name="save_quote" /> Save as a quotation</label>
          {error && <div className="form-error">{error}</div>}
          <button className="primary full-button" disabled={working || !rates.length}>{working ? "Pricing…" : "Calculate freight"}</button>
        </form>
        {quote && <div className="quote-result">
          {[["Freight", quote.freight], ["Fuel surcharge", quote.fuel_surcharge], ["Loading & unloading", quote.handling_charges], ["Other charges", quote.other_charges], ["Taxable value", quote.taxable_value], [quote.reverse_charge ? "GST (reverse charge)" : `GST @ ${quote.gst_percent}%`, quote.gst_amount]].map(row => <div className="quote-line" key={String(row[0])}><span>{row[0]}</span><strong>{rupees(row[1])}</strong></div>)}
          <div className="invoice-total"><span>Total payable</span><strong>{rupees(quote.total)}</strong><small>{quote.reverse_charge ? "GST payable by consignee under RCM" : "Inclusive of GST"}</small></div>
        </div>}
      </aside>
    </div>
  </div>;
}

function ComplianceView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: (message: string) => void; openAction: (type: string) => void }) {
  const [documents, setDocuments] = useState<any[]>([]);
  const [documentTotal, setDocumentTotal] = useState(0);
  const [expiring, setExpiring] = useState<any[]>([]);
  const [due, setDue] = useState<any[]>([]);
  const [horizon, setHorizon] = useState(30);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    Promise.all([
      fmsRequest<any>(wholeSet("compliance-documents/")).then(payload => {
        const records = asList(payload);
        setDocuments(records);
        setDocumentTotal(asCount(payload, records));
      }).catch(() => undefined),
      fmsRequest<any>(`compliance-documents/expiring/?days=${horizon}`).then(payload => setExpiring(payload.documents || [])).catch(() => undefined),
      fmsRequest<any>("maintenance-schedules/due/").then(payload => setDue(payload.schedules || [])).catch(() => undefined),
    ]).finally(() => setLoading(false));
  }, [reloadKey, horizon]);

  const expired = expiring.filter(document => document.status === "expired");
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">STATUTORY COMPLIANCE</p><h2>Vehicle & driver documents</h2><p>RC, insurance, fitness, national permit, PUC, FASTag KYC and driving licences with renewal alerts.</p></div><button className="primary module-action" onClick={() => openAction("document")}>＋ Add document</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Documents tracked</span><strong>{loading ? "—" : documentTotal}</strong><small>Across vehicles and drivers</small></div>
      <div className="module-stat alert"><span>Expired</span><strong>{loading ? "—" : expired.length}</strong><small>Vehicle must be taken off road</small></div>
      <div className="module-stat warn"><span>Due in {horizon} days</span><strong>{loading ? "—" : expiring.length - expired.length}</strong><small>Start the renewal now</small></div>
    </div>
    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>Renewal watchlist</strong><span>{expiring.length} documents need attention</span></div>
        <div className="toolbar-actions">{[15, 30, 60, 90].map(days => <button key={days} className={days === horizon ? "chip active" : "chip"} onClick={() => setHorizon(days)}>{days} days</button>)}</div></div>
      <div className="table-wrap"><table><thead><tr><th>Document</th><th>Belongs to</th><th>Number</th><th>Expires</th><th>Days left</th><th>Status</th></tr></thead>
        <tbody>{expiring.map(document => <tr key={document.id}>
          <td><strong>{String(document.document_type).replaceAll("_", " ")}</strong></td>
          <td>{document.vehicle_number || document.driver_name || "—"}</td>
          <td>{document.number || "—"}</td>
          <td>{document.expiry_date}</td>
          <td>{document.days_to_expiry}</td>
          <td><span className={"status " + document.status}>{document.status}</span></td>
        </tr>)}</tbody></table></div>
      {!loading && !expiring.length && <div className="data-state">Every document is valid beyond the selected window.</div>}
    </section>
    <section className="module-table-card compliance-second">
      <div className="module-toolbar"><div><strong>Preventive maintenance due</strong><span>{due.length} schedules</span></div><button className="chip" onClick={() => onAction("Maintenance plan reviewed")}>Review plan</button></div>
      <div className="table-wrap"><table><thead><tr><th>Vehicle</th><th>Task</th><th>Next due km</th><th>Km remaining</th><th>Next due date</th></tr></thead>
        <tbody>{due.map(schedule => <tr key={schedule.id}>
          <td><strong>{schedule.vehicle_number}</strong></td><td>{schedule.task}</td>
          <td>{Number(schedule.next_due_km).toLocaleString("en-IN")}</td>
          <td>{schedule.km_remaining ?? "—"}</td><td>{schedule.next_due_date || "—"}</td>
        </tr>)}</tbody></table></div>
      {!loading && !due.length && <div className="data-state">No preventive service is due right now.</div>}
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
          {navGroups.map(group => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{group.label}</p>
              {group.items.map(([item, icon]) => (
                <button key={item} className={active === item ? "nav-item active" : "nav-item"} onClick={() => { setActive(item); show(`${item} view selected`); }}>
                  <span className="nav-icon">{icon}</span>{item}
                  {item === "Compliance" && Number(dashboard?.documents_expiring) > 0 && <span className="badge">{dashboard.documents_expiring}</span>}
                  {item === "Issues" && Number(dashboard?.open_issues) > 0 && <span className="badge">{dashboard.open_issues}</span>}
                </button>
              ))}
            </div>
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

        {active === "Overview" ? <div className="page-grid"><section className="quick-actions"><div><p className="eyebrow">QUICK ACTIONS</p><h2>Run daily fleet operations</h2></div><button onClick={() => setAction("lr")}><span>▤</span><b>Generate LR</b><small>Book consignment</small></button><button onClick={() => setAction("trip")}><span>▦</span><b>Create trip sheet</b><small>Allocate vehicle & driver</small></button><button onClick={() => setAction("invoice")}><span>₹</span><b>Generate invoice</b><small>Bill a completed trip</small></button><button onClick={() => setAction("tracking")}><span>⌖</span><b>Track vehicles</b><small>View live GPS map</small></button><button onClick={() => setAction("order")}><span>◈</span><b>Book order</b><small>FleetOps consignment</small></button><button onClick={() => setAction("fuel")}><span>⛽</span><b>Log diesel</b><small>Fuel & mileage entry</small></button></section>
          <section className="hero-card">
            <div><span className="live-pill"><i /> LIVE FLEET</span><h2>{dashboard?.vehicles_on_trip ?? "—"} of {dashboard?.vehicles ?? "—"} vehicles<br />are on the road</h2><p>{dashboard ? Math.round((dashboard.vehicles_on_trip / Math.max(dashboard.vehicles, 1)) * 100) : "—"}% fleet utilisation · {dashboard?.active_trips ?? "—"} active trips</p><button className="text-button" onClick={() => show("Live operations opened")}>View live operations <span>→</span></button></div>
            <div className="fleet-visual" aria-label="Fleet utilisation 78 percent"><div className="ring"><strong>{dashboard ? Math.round((dashboard.vehicles_on_trip / Math.max(dashboard.vehicles, 1)) * 100) : "—"}%</strong><span>utilised</span></div><div className="route-line"><span className="pin one" /><span className="truck">▰</span><span className="pin two" /></div></div>
          </section>

          <section className="metric-card"><div className="metric-top"><span className="metric-icon green">₹</span><span className="trend up">↗ 12.4%</span></div><p>Total invoiced</p><h3>₹{Number(dashboard?.invoice_total || 0).toLocaleString("en-IN")}</h3><small>{dashboard?.open_invoices ?? 0} open invoices</small></section>
          <section className="metric-card"><div className="metric-top"><span className="metric-icon blue">↗</span><span className="trend down">↘ 3.1%</span></div><p>Pending settlements</p><h3>₹{Number(dashboard?.pending_settlements || 0).toLocaleString("en-IN")}</h3><small>Driver and trip expenses</small></section>
          <section className="metric-card profit"><div className="metric-top"><span className="metric-icon violet">◎</span><span className="trend up">↗ 2.8%</span></div><p>Available vehicles</p><h3>{dashboard?.available_vehicles ?? "—"}</h3><small>Ready for allocation</small></section>

          <section className="workflow-card">
            <div className="section-heading"><div><p className="eyebrow">TODAY&apos;S WORKFLOW</p><h2>Keep operations moving</h2></div><button className="more">•••</button></div>
            <div className="workflow-list">{liveWorkflows(dashboard).map((flow, i) => <button key={flow.name} className="workflow-row" onClick={() => setActive(flow.target)}><span className={`step ${flow.accent}`}>{String(i + 1).padStart(2,"0")}</span><span className="workflow-copy"><strong>{flow.name}</strong><small>{flow.detail}</small></span><span className={`flow-value ${flow.accent}`}>{flow.value}</span><span className="arrow">→</span></button>)}</div>
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
        </div> : active === "Modules" ? <FeatureHub onAction={show} /> : active === "Orders" ? <OrdersView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Rates" ? <RatesView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Compliance" ? <ComplianceView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : fleetOpsPages.includes(active) ? <FleetOpsView name={active} reloadKey={dataVersion} onAction={show} openAction={setAction} /> : <ModuleView name={active as keyof typeof modules} reloadKey={dataVersion} onAction={show} openAction={setAction} />}
      </section>
      {action && <ActionPanel type={action} onClose={() => setAction("")} onCreated={() => setDataVersion(v => v + 1)} onDone={(message) => { show(message); if (action !== "tracking") setAction(""); }} />}
      {toast && <div className="toast">✓ {toast}</div>}
    </main>
  );
}

