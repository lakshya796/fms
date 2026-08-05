"use client";

import { useEffect, useRef, useState } from "react";
import { UNAUTHORISED_EVENT, fmsRequest, login, logout } from "./lib/fms-api";

const navGroups: { label: string; items: [string, string][] }[] = [
  { label: "WORKSPACE", items: [["Overview", "⌂"], ["Analytics", "◎"]] },
  { label: "TRANSPORT", items: [["Indents", "◰"], ["Dispatch", "▦"], ["Orders", "◈"], ["ePOD", "✍"], ["Tracking", "⌖"], ["Operations", "▤"]] },
  { label: "COMMERCIAL", items: [["Customers", "◇"], ["Sales", "↗"], ["Rates", "⚖"], ["Invoices", "▥"]] },
  { label: "FLEET", items: [["Fleet", "▱"], ["Fleets", "▩"], ["Drivers", "♙"], ["Maintenance", "⚒"], ["Compliance", "▣"], ["Fuel", "⛽"], ["Issues", "⚠"]] },
  { label: "NETWORK", items: [["Vendors", "⌸"], ["Places", "⌂"], ["Service areas", "◫"], ["Zones", "◍"]] },
  { label: "FINANCE", items: [["Expenses", "▤"], ["Settlements", "₹"]] },
  { label: "ACCOUNTS", items: [["Ledger", "▦"], ["Vouchers", "▤"], ["Vendor bills", "◳"], ["Payments", "⇄"], ["Financials", "◫"]] },
  { label: "ADMIN", items: [["Users", "♟"], ["Roles", "⚿"], ["Branches", "⌸"], ["Audit trail", "◷"]] },
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
  "Service areas": {
    eyebrow: "OPERATING REGIONS", title: "Service areas", action: "+ Add service area", actionType: "servicearea",
    blurb: "The regions you operate in. Zones, rate cards and places all hang off a service area.",
    columns: ["Service area", "Code", "States covered", "Zones", "Status"],
  },
  Zones: {
    eyebrow: "GEOFENCING", title: "Zones & geofences", action: "+ Add zone", actionType: "zone",
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
  Ledger: {
    eyebrow: "CHART OF ACCOUNTS", title: "Ledger accounts", action: "+ Add account", actionType: "account",
    blurb: "Assets, liabilities, income and expense heads with their live balances.",
    columns: ["Account", "Name", "Type", "Balance", "Status"],
  },
  "Vendor bills": {
    eyebrow: "PAYABLES", title: "Vendor bills", action: "+ Record bill", actionType: "bill",
    blurb: "Purchase invoices from attached transporters, workshops and fuel vendors, with TDS.",
    columns: ["Bill", "Vendor", "Date", "Balance due", "Status"],
  },
  Branches: {
    eyebrow: "ORGANISATION", title: "Branches & depots", action: "+ Add branch", actionType: "branch",
    blurb: "Each depot books its own consignments and reports its own numbers.",
    columns: ["Branch", "Code", "City", "Staff", "Status"],
  },
  "Audit trail": {
    eyebrow: "GOVERNANCE", title: "Audit trail", action: "", actionType: "",
    blurb: "Every create, update and delete, with the person who did it.",
    columns: ["When", "User", "Action", "Record", "Detail"],
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
    blurb: "Raised from the consignment, so freight and GST always match the rate card. Bill a delivered order from its drawer on the Orders board.",
    columns: ["Invoice", "Customer", "Against", "Due date", "Amount", "Payment status"],
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

function FeatureHub({ onAction }: { onAction: Notify }) {
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
  tracking: { eyebrow: "LIVE GPS", title: "Vehicle tracking", button: "Refresh location" },
};

type FormField = { name: string; label: string; type?: "text" | "number" | "date" | "datetime" | "select" | "textarea"; options?: [string, string][]; source?: string; value?: string; required?: boolean; multiple?: boolean };
type FormSpec = { eyebrow: string; title: string; button: string; endpoint: string; fields: FormField[]; reference: (values: Record<string, string>, created: any) => string };

const sourceLabel: Record<string, (record: any) => string> = {
  "customers/": r => r.name, "vehicles/": r => r.registration_number, "drivers/": r => r.name,
  "places/": r => `${r.name} · ${r.city}`, "service-areas/": r => r.name, "service-rates/": r => r.name,
  "fleets/": r => r.name, "trips/": r => r.number, "vendors/": r => r.name, "orders/": r => r.number,
  "lorry-receipts/": r => `${r.number} · ${r.origin} → ${r.destination}`,
  "accounting/accounts/": r => `${r.code} ${r.name}`,
  "accounting/accounts/?account_type=expense": r => `${r.code} ${r.name}`,
  "accounting/cost-centres/": r => r.name,
  "iam/branches/": r => `${r.name} (${r.code})`,
  "iam/roles/": r => r.name,
};

const today = () => new Date().toISOString().slice(0, 10);

const initials = (name: string) =>
  name.split(/[\s.@_-]+/).filter(Boolean).slice(0, 2).map(part => part[0]?.toUpperCase()).join("") || "?";

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
  servicearea: {
    eyebrow: "OPERATING REGIONS", title: "Add service area", button: "Save service area", endpoint: "service-areas/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Service area name", required: true },
      { name: "code", label: "Code", required: true },
      { name: "states", label: "States covered", value: "Maharashtra, Gujarat" },
      { name: "description", label: "Description" },
      { name: "status", label: "Status", type: "select", options: [["active", "Active"], ["inactive", "Inactive"]] },
    ],
  },
  rate: {
    eyebrow: "RATE MANAGEMENT", title: "Create rate card", button: "Save rate card", endpoint: "service-rates/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Rate card name", required: true },
      { name: "code", label: "Code", required: true },
      { name: "service_area", label: "Service area", type: "select", source: "service-areas/" },
      { name: "customer", label: "Customer (optional)", type: "select", source: "customers/" },
      { name: "vehicle_type", label: "Vehicle type", value: "32 ft MXL container" },
      { name: "rate_type", label: "Charged on", type: "select", options: [["per_km", "Per km"], ["per_ton_km", "Per ton per km"], ["per_kg", "Per kg"], ["per_trip", "Fixed per trip"], ["per_hour", "Per hour"]] },
      { name: "base_charge", label: "Base charge (₹)", type: "number", value: "2500" },
      { name: "per_km_rate", label: "Per km (₹)", type: "number", value: "48" },
      { name: "per_ton_km_rate", label: "Per ton-km (₹)", type: "number", value: "6.50" },
      { name: "per_kg_rate", label: "Per kg (₹)", type: "number", value: "0" },
      { name: "per_hour_rate", label: "Per hour (₹)", type: "number", value: "0" },
      { name: "minimum_charge", label: "Minimum charge (₹)", type: "number", value: "8000" },
      { name: "loading_charge", label: "Loading (₹)", type: "number", value: "1800" },
      { name: "unloading_charge", label: "Unloading (₹)", type: "number", value: "1500" },
      { name: "halting_charge_per_day", label: "Halting per day (₹)", type: "number", value: "2500" },
      { name: "fuel_surcharge_percent", label: "Fuel surcharge %", type: "number", value: "3.5" },
      { name: "gst_percent", label: "GST %", type: "select", options: [["5", "5% (GTA without ITC)"], ["12", "12% (with ITC)"], ["18", "18%"], ["0", "Exempt"]] },
      { name: "reverse_charge", label: "GST under reverse charge", type: "select", options: [["false", "No, we charge GST"], ["true", "Yes, consignee pays under RCM"]] },
      { name: "effective_from", label: "Effective from", type: "date" },
      { name: "effective_to", label: "Effective to", type: "date" },
    ],
  },
  driver: {
    eyebrow: "DRIVER MASTER", title: "Add driver", button: "Save driver", endpoint: "drivers/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Driver name", required: true },
      { name: "phone", label: "Phone", required: true },
      { name: "licence_number", label: "Licence number", required: true },
      { name: "licence_expiry", label: "Licence valid until", type: "date" },
      { name: "status", label: "Status", type: "select", options: [["available", "Available"], ["on_trip", "On trip"], ["leave", "On leave"], ["inactive", "Inactive"]] },
      { name: "home_city", label: "Home city" },
      { name: "date_of_joining", label: "Date of joining", type: "date" },
      { name: "monthly_salary", label: "Monthly salary (₹)", type: "number", value: "22000" },
      { name: "daily_allowance", label: "Daily bhatta (₹)", type: "number", value: "600" },
      { name: "aadhaar_number", label: "Aadhaar number" },
    ],
  },
  schedule: {
    eyebrow: "PREVENTIVE MAINTENANCE", title: "Add service schedule", button: "Save schedule", endpoint: "maintenance-schedules/",
    reference: values => values.task,
    fields: [
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/", required: true },
      { name: "task", label: "Service task", value: "Engine oil and filter change", required: true },
      { name: "interval_km", label: "Every (km)", type: "number", value: "20000" },
      { name: "interval_days", label: "Or every (days)", type: "number", value: "180" },
      { name: "last_service_km", label: "Last service odometer", type: "number" },
      { name: "last_service_date", label: "Last service date", type: "date" },
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
  customer: {
    eyebrow: "CUSTOMER KYC", title: "Add customer", button: "Save customer", endpoint: "customers/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Customer name", required: true },
      { name: "gstin", label: "GSTIN", required: true },
      { name: "pan", label: "PAN" },
      { name: "phone", label: "Phone" },
      { name: "email", label: "Email" },
      { name: "credit_limit", label: "Credit limit (₹)", type: "number", value: "500000" },
      { name: "kyc_status", label: "KYC status", type: "select", options: [["pending", "Pending verification"], ["verified", "Verified"], ["rejected", "Rejected"]] },
      { name: "billing_address", label: "Billing address", type: "textarea" },
    ],
  },
  vehicle: {
    eyebrow: "FLEET MASTER", title: "Add vehicle", button: "Save vehicle", endpoint: "vehicles/",
    reference: values => values.registration_number,
    fields: [
      { name: "registration_number", label: "Registration number", required: true },
      { name: "vehicle_type", label: "Vehicle type", value: "32 ft MXL container", required: true },
      { name: "make_model", label: "Make and model" },
      { name: "capacity_kg", label: "Capacity (kg)", type: "number", value: "16000" },
      { name: "ownership", label: "Ownership", type: "select", options: [["owned", "Owned"], ["attached", "Attached / vendor"], ["leased", "Leased"]] },
      { name: "status", label: "Status", type: "select", options: [["available", "Available"], ["on_trip", "On trip"], ["maintenance", "In workshop"], ["inactive", "Inactive"]] },
      { name: "fuel_type", label: "Fuel", type: "select", options: [["diesel", "Diesel"], ["cng", "CNG"], ["lng", "LNG"], ["electric", "Electric"]] },
      { name: "current_odometer_km", label: "Odometer (km)", type: "number", value: "0" },
      { name: "fastag_id", label: "FASTag ID" },
      { name: "gps_device_id", label: "GPS device ID" },
      { name: "chassis_number", label: "Chassis number" },
      { name: "engine_number", label: "Engine number" },
      { name: "insurance_expiry", label: "Insurance valid until", type: "date" },
      { name: "permit_expiry", label: "Permit valid until", type: "date" },
    ],
  },
  quote: {
    eyebrow: "SALES", title: "Create quotation", button: "Save quotation", endpoint: "quotes/",
    reference: (_values, created) => created?.number || "Quotation",
    fields: [
      { name: "number", label: "Quotation number", value: "QTN-" + Date.now().toString().slice(-5), required: true },
      { name: "customer", label: "Customer", type: "select", source: "customers/", required: true },
      { name: "origin", label: "Origin", required: true },
      { name: "destination", label: "Destination", required: true },
      { name: "freight_amount", label: "Freight (₹)", type: "number", required: true },
      { name: "valid_until", label: "Valid until", type: "date", required: true },
      { name: "status", label: "Stage", type: "select", options: [["draft", "Draft"], ["sent", "Sent"], ["negotiation", "Negotiation"], ["accepted", "Accepted"], ["lost", "Lost"]] },
    ],
  },
  lr: {
    eyebrow: "CONSIGNMENT BOOKING", title: "Book a lorry receipt", button: "Generate LR", endpoint: "lorry-receipts/",
    reference: (_values, created) => created?.number || "LR",
    fields: [
      { name: "number", label: "LR number", value: "LR-" + Date.now().toString().slice(-6), required: true },
      { name: "customer", label: "Customer", type: "select", source: "customers/", required: true },
      { name: "consignor", label: "Consignor", required: true },
      { name: "consignee", label: "Consignee", required: true },
      { name: "origin", label: "Origin", required: true },
      { name: "destination", label: "Destination", required: true },
      { name: "material", label: "Material", required: true },
      { name: "weight_kg", label: "Weight (kg)", type: "number", required: true },
      { name: "packages", label: "Packages", type: "number", value: "1" },
      { name: "eway_bill_number", label: "E-way bill number" },
      { name: "freight_amount", label: "Freight (₹)", type: "number" },
      { name: "status", label: "Status", type: "select", options: [["booked", "Booked"], ["dispatched", "Dispatched"], ["delivered", "Delivered"]] },
    ],
  },
  trip: {
    eyebrow: "DISPATCH PLANNING", title: "Create trip sheet", button: "Create trip sheet", endpoint: "trips/",
    reference: (_values, created) => created?.number || "Trip",
    fields: [
      { name: "number", label: "Trip number", value: "TRP-" + Date.now().toString().slice(-5), required: true },
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/", required: true },
      { name: "driver", label: "Driver", type: "select", source: "drivers/", required: true },
      { name: "lorry_receipts", label: "Consignments (LRs) on this trip", type: "select", source: "lorry-receipts/", multiple: true },
      { name: "origin", label: "Origin", required: true },
      { name: "destination", label: "Destination", required: true },
      { name: "planned_departure", label: "Planned departure", type: "datetime", required: true },
      { name: "advance_amount", label: "Trip advance (₹)", type: "number", value: "0" },
      { name: "estimated_cost", label: "Estimated cost (₹)", type: "number", value: "0" },
      { name: "status", label: "Status", type: "select", options: [["planned", "Planned"], ["dispatched", "Dispatched"], ["in_transit", "In transit"], ["closed", "Closed"]] },
    ],
  },
  invoice: {
    eyebrow: "FREIGHT BILLING", title: "Generate customer invoice", button: "Generate invoice", endpoint: "invoices/",
    reference: (_values, created) => created?.number || "Invoice",
    fields: [
      { name: "number", label: "Invoice number", value: "INV-" + Date.now().toString().slice(-6), required: true },
      { name: "customer", label: "Customer", type: "select", source: "customers/", required: true },
      { name: "trip", label: "Trip", type: "select", source: "trips/", required: true },
      { name: "freight_amount", label: "Freight (₹)", type: "number", required: true },
      { name: "additional_charges", label: "Additional charges (₹)", type: "number", value: "0" },
      { name: "tax_amount", label: "GST (₹)", type: "number", value: "0" },
      { name: "total_amount", label: "Invoice total (₹)", type: "number", required: true },
      { name: "due_date", label: "Due date", type: "date", required: true },
      { name: "status", label: "Status", type: "select", options: [["draft", "Draft"], ["issued", "Issued"], ["paid", "Paid"], ["overdue", "Overdue"]] },
    ],
  },
  settlement: {
    eyebrow: "DRIVER ACCOUNTS", title: "Create driver settlement", button: "Save settlement", endpoint: "settlements/",
    reference: values => `Settlement for ₹${values.net_payable || 0}`,
    fields: [
      { name: "driver", label: "Driver", type: "select", source: "drivers/", required: true },
      { name: "trip", label: "Trip", type: "select", source: "trips/", required: true },
      { name: "advance_amount", label: "Advance paid (₹)", type: "number", value: "0" },
      { name: "approved_expenses", label: "Approved expenses (₹)", type: "number", value: "0" },
      { name: "net_payable", label: "Net payable (₹)", type: "number", value: "0" },
      { name: "status", label: "Status", type: "select", options: [["pending", "Pending"], ["approved", "Approved"], ["paid", "Paid"]] },
    ],
  },
  maintenance: {
    eyebrow: "FLEET MAINTENANCE", title: "Create work order", button: "Save work order", endpoint: "maintenance/",
    reference: (_values, created) => created?.number || "Work order",
    fields: [
      { name: "number", label: "Work order number", value: "WO-" + Date.now().toString().slice(-5), required: true },
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/", required: true },
      { name: "title", label: "Work description", value: "Preventive service", required: true },
      { name: "category", label: "Category", type: "select", options: [["preventive", "Preventive"], ["breakdown", "Breakdown"], ["accident", "Accident repair"], ["tyre", "Tyre"], ["bodywork", "Bodywork"]] },
      { name: "scheduled_date", label: "Scheduled date", type: "date", required: true },
      { name: "odometer_km", label: "Odometer (km)", type: "number", value: "0" },
      { name: "estimated_cost", label: "Estimated cost (₹)", type: "number", value: "0" },
      { name: "vendor", label: "Workshop / vendor" },
      { name: "status", label: "Status", type: "select", options: [["open", "Open"], ["in_progress", "In progress"], ["completed", "Completed"]] },
    ],
  },
  user: {
    eyebrow: "USER MANAGEMENT", title: "Add user", button: "Create user", endpoint: "iam/users/",
    reference: values => values.username,
    fields: [
      { name: "username", label: "Login username", required: true },
      { name: "password", label: "Password (min 10 characters)", required: true },
      { name: "first_name", label: "First name" },
      { name: "last_name", label: "Last name" },
      { name: "email", label: "Email" },
      { name: "employee_code", label: "Employee code", required: true },
      { name: "phone", label: "Phone" },
      { name: "designation", label: "Designation" },
      { name: "role", label: "Role", type: "select", source: "iam/roles/" },
      { name: "branch", label: "Branch", type: "select", source: "iam/branches/" },
      { name: "restrict_to_branch", label: "Restrict to own branch", type: "select", options: [["false", "No, sees all branches"], ["true", "Yes, own branch only"]] },
    ],
  },
  account: {
    eyebrow: "CHART OF ACCOUNTS", title: "Add ledger account", button: "Save account", endpoint: "accounting/accounts/",
    reference: values => `${values.code} ${values.name}`,
    fields: [
      { name: "code", label: "Account code", required: true },
      { name: "name", label: "Account name", required: true },
      { name: "account_type", label: "Type", type: "select", options: [["asset", "Asset"], ["liability", "Liability"], ["equity", "Equity"], ["income", "Income"], ["expense", "Expense"]] },
      { name: "parent", label: "Group under", type: "select", source: "accounting/accounts/" },
      { name: "opening_balance", label: "Opening balance (₹)", type: "number", value: "0" },
      { name: "description", label: "Description" },
    ],
  },
  bill: {
    eyebrow: "PAYABLES", title: "Record vendor bill", button: "Save bill", endpoint: "accounting/vendor-bills/",
    reference: values => values.number,
    fields: [
      { name: "number", label: "Bill number", required: true },
      { name: "vendor", label: "Vendor", type: "select", source: "vendors/", required: true },
      { name: "bill_date", label: "Bill date", type: "date", value: today(), required: true },
      { name: "due_date", label: "Due date", type: "date" },
      { name: "expense_account", label: "Booked to (expense head)", type: "select", source: "accounting/accounts/?account_type=expense" },
      { name: "cost_centre", label: "Cost centre", type: "select", source: "accounting/cost-centres/" },
      { name: "taxable_amount", label: "Taxable value (₹)", type: "number", required: true },
      { name: "gst_amount", label: "GST (₹)", type: "number", value: "0" },
      { name: "tds_amount", label: "TDS deducted (₹)", type: "number", value: "0" },
      { name: "narration", label: "Narration" },
    ],
  },
  branch: {
    eyebrow: "ORGANISATION", title: "Add branch", button: "Save branch", endpoint: "iam/branches/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Branch name", required: true },
      { name: "code", label: "Branch code", required: true },
      { name: "branch_type", label: "Type", type: "select", options: [["branch", "Branch"], ["head_office", "Head office"], ["depot", "Depot"], ["warehouse", "Warehouse"], ["workshop", "Workshop"]] },
      { name: "city", label: "City", required: true },
      { name: "state", label: "State" },
      { name: "pincode", label: "Pincode" },
      { name: "gstin", label: "Branch GSTIN" },
      { name: "phone", label: "Phone" },
      { name: "manager", label: "Branch manager" },
      { name: "address", label: "Address", type: "textarea" },
    ],
  },
  costcentre: {
    eyebrow: "COSTING", title: "Add cost centre", button: "Save cost centre", endpoint: "accounting/cost-centres/",
    reference: values => values.name,
    fields: [
      { name: "name", label: "Cost centre name", required: true },
      { name: "code", label: "Code", required: true },
      { name: "centre_type", label: "Type", type: "select", options: [["vehicle", "Vehicle"], ["branch", "Branch"], ["route", "Route"], ["driver", "Driver"], ["other", "Other"]] },
      { name: "vehicle", label: "Vehicle", type: "select", source: "vehicles/" },
      { name: "branch", label: "Branch", type: "select", source: "iam/branches/" },
    ],
  },
  indent: {
    eyebrow: "DEMAND CAPTURE", title: "Raise an indent", button: "Save indent", endpoint: "indents/",
    reference: (_values, created) => created?.number || "Indent",
    fields: [
      { name: "customer", label: "Customer", type: "select", source: "customers/", required: true },
      { name: "branch", label: "Branch", type: "select", source: "iam/branches/" },
      { name: "pickup", label: "Loading point", type: "select", source: "places/", required: true },
      { name: "dropoff", label: "Unloading point", type: "select", source: "places/", required: true },
      { name: "vehicle_type", label: "Vehicle required", value: "32 ft MXL container" },
      { name: "vehicles_required", label: "How many", type: "number", value: "1" },
      { name: "material", label: "Material" },
      { name: "weight_kg", label: "Weight (kg)", type: "number", value: "12000" },
      { name: "required_at", label: "Required by", type: "datetime" },
      { name: "expected_rate", label: "Expected freight (₹)", type: "number", value: "0" },
      { name: "service_rate", label: "Rate card", type: "select", source: "service-rates/" },
      { name: "remarks", label: "Remarks", type: "textarea" },
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
      if (field.multiple) {
        const chosen = form.getAll(field.name).map(entry => Number(entry)).filter(entry => !Number.isNaN(entry));
        values[field.name] = chosen.join(",");
        if (chosen.length) payload[field.name] = chosen;
        continue;
      }
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
      {field.source ? <select name={field.name} required={field.required} multiple={field.multiple} defaultValue={field.multiple ? [] : ""}>
        {!field.multiple && <option value="">{(options[field.source] || []).length ? "Select…" : "Loading…"}</option>}
        {(options[field.source] || []).map(record => <option key={record.id} value={record.id}>{sourceLabel[field.source!] ? sourceLabel[field.source!](record) : record.name}</option>)}
      </select>
      : field.type === "select" ? <select name={field.name} defaultValue={field.value || (field.options || [["", ""]])[0][0]}>{(field.options || []).map(option => <option key={option[0]} value={option[0]}>{option[1]}</option>)}</select>
      : <input name={field.name} type={field.type === "number" ? "number" : field.type === "date" ? "date" : field.type === "datetime" ? "datetime-local" : "text"} step={field.type === "number" ? "any" : undefined} defaultValue={field.value || ""} required={field.required} />}
    </label>)}</div>
    {spec.fields.filter(field => field.type === "textarea").map(field => <label key={field.name}>{field.label}<textarea name={field.name} defaultValue={field.value || ""} /></label>)}
    {error && <div className="form-error">{error}</div>}
    <div className="form-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" type="submit" disabled={working}>{working ? "Saving…" : spec.button}</button></div>
  </form>;
}

function ActionPanel({ type, onClose, onDone, onCreated }: { type: string; onClose: () => void; onDone: (message: string) => void; onCreated: () => void }) {
  const [complete, setComplete] = useState(false);
  const [reference, setReference] = useState("");
  const [vehicle, setVehicle] = useState("");
  const [liveTrip, setLiveTrip] = useState<any>(null);
  const spec = recordForms[type];
  const meta = actionMeta[type] || spec;
  useEffect(() => {
    if (type !== "tracking") return;
    fmsRequest<any>("trips/").then(payload => {
      const trip = asList(payload)[0] || null;
      setLiveTrip(trip);
      if (trip?.vehicle_number) setVehicle(trip.vehicle_number);
    }).catch(() => undefined);
  }, [type]);
  if (!meta) return null;

  return <div className="modal-backdrop" onMouseDown={onClose}><section className={"action-panel " + (type === "tracking" ? "map-panel" : "")} onMouseDown={event => event.stopPropagation()}>
    <div className="panel-head"><div><p className="eyebrow">{meta.eyebrow}</p><h2>{meta.title}</h2></div><button className="panel-close" onClick={onClose}>×</button></div>
    {type === "tracking" ? <div className="tracking-layout">
      <div className="mock-map"><div className="map-road r1"/><div className="map-road r2"/><div className="map-road r3"/><span className="city mumbai">Mumbai</span><span className="city pune">Pune</span><span className="map-pin start">●</span><span className="map-pin vehicle">▰</span><span className="map-pin finish">●</span><div className="map-progress"/></div>
      <div className="vehicle-list"><label>Track vehicle<select value={vehicle} onChange={event => setVehicle(event.target.value)}><option>{liveTrip?.vehicle_number || "No assigned vehicle"}</option></select></label><div className="tracking-stat"><span>Status</span><strong>{liveTrip?.status?.replaceAll("_", " ") || "Loading live trip…"}</strong></div><div className="tracking-grid"><div><span>Speed</span><strong>{liveTrip?.tracking_events?.[0]?.speed_kph || 0} km/h</strong></div><div><span>Route</span><strong>{liveTrip ? liveTrip.origin + " → " + liveTrip.destination : "—"}</strong></div><div><span>Last update</span><strong>{liveTrip?.tracking_events?.[0]?.recorded_at ? new Date(liveTrip.tracking_events[0].recorded_at).toLocaleTimeString("en-IN") : "No GPS ping"}</strong></div><div><span>Trip</span><strong>{liveTrip?.number || "—"}</strong></div></div><div className="event-feed"><strong>Live trip events</strong>{(liveTrip?.tracking_events || []).slice(0, 4).map((event: any) => <p key={event.id}><i/>{event.description || event.event_type}<span>{new Date(event.recorded_at).toLocaleTimeString("en-IN")}</span></p>)}{liveTrip && !liveTrip.tracking_events?.length && <p>No GPS events received yet</p>}</div><button className="primary full-button" onClick={() => { setLiveTrip(null); fmsRequest<any>("trips/").then(payload => setLiveTrip(asList(payload)[0] || null)); onDone("GPS location refreshed"); }}>{meta.button}</button></div>
    </div> : complete ? <div className="success-state"><span>✓</span><h3>{reference}</h3><p>Saved to the live fleet database. The module list has been refreshed.</p><div className="success-actions"><button className="secondary" onClick={onClose}>Close</button><button className="primary" onClick={() => { setComplete(false); setReference(""); }}>Add another</button></div></div>
      : <RecordForm spec={spec} onClose={onClose} onSaved={newReference => { setReference(newReference); setComplete(true); onCreated(); }} />}
  </section></div>;
}

const liveModules: Record<string, { endpoint: string; map: (record: any) => string[] }> = {
  Customers: { endpoint: "customers/", map: r => [r.name, r.gstin, "₹" + Number(r.credit_limit).toLocaleString("en-IN"), r.email || "—", r.kyc_status] },
  Sales: { endpoint: "quotes/", map: r => [r.number, r.customer_name, r.origin + " → " + r.destination, "₹" + Number(r.freight_amount).toLocaleString("en-IN"), r.status] },
  Operations: { endpoint: "lorry-receipts/", map: r => [r.number, r.consignor + " → " + r.consignee, r.origin + " → " + r.destination, r.eway_bill_number || "—", r.status] },
  Fleet: { endpoint: "vehicles/", map: r => [r.registration_number, r.vehicle_type, r.ownership, Number(r.capacity_kg).toLocaleString("en-IN") + " kg", r.status] },
  Settlements: { endpoint: "settlements/", map: r => [r.driver_name, "Trip #" + r.trip, "₹" + Number(r.advance_amount).toLocaleString("en-IN"), "₹" + Number(r.approved_expenses).toLocaleString("en-IN"), r.status] },
  Invoices: { endpoint: "invoices/", map: r => [r.number, r.customer_name, r.order_number || r.trip_number || "manual", r.due_date, rupees(r.total_amount), r.status] },
  Vendors: { endpoint: "vendors/", map: r => [r.name, r.vendor_type, r.city || "—", r.gstin || "—", r.status] },
  Places: { endpoint: "places/", map: r => [r.name, r.place_type, r.city, r.pincode || "—", r.status] },
  "Service areas": { endpoint: "service-areas/", map: r => [r.name, r.code, r.states || "—", String(r.zone_count ?? 0), r.status] },
  Zones: { endpoint: "zones/", map: r => [r.name, r.service_area_name, Number(r.center_latitude).toFixed(3) + ", " + Number(r.center_longitude).toFixed(3), r.radius_km + " km", r.zone_type] },
  Fleets: { endpoint: "fleets/", map: r => [r.name, r.service_area_name || "—", String(r.vehicle_count), String(r.driver_count), r.status] },
  Fuel: { endpoint: "fuel-entries/", map: r => [r.vehicle_number, r.entry_date, Number(r.volume_litres).toFixed(2) + " L", Number(r.mileage_kmpl) ? Number(r.mileage_kmpl).toFixed(2) + " km/l" : "—", r.payment_method] },
  Expenses: { endpoint: "trip-expenses/", map: r => [r.category.replaceAll("_", " "), r.vehicle_number || "—", r.expense_date, "₹" + Number(r.amount).toLocaleString("en-IN"), r.status] },
  Issues: { endpoint: "issues/", map: r => [r.number, r.vehicle_number || "—", r.issue_type, r.priority, r.status] },
  Ledger: { endpoint: "accounting/accounts/", map: r => [r.code, r.is_group ? `${r.name} (group)` : r.name, r.account_type, rupees(r.current_balance), r.is_active ? "active" : "inactive"] },
  "Vendor bills": { endpoint: "accounting/vendor-bills/", map: r => [r.number, r.vendor_name, r.bill_date, rupees(r.balance_due), r.status] },
  Branches: { endpoint: "iam/branches/", map: r => [r.name, r.code, r.city, String(r.staff_count ?? 0), r.status] },
  "Audit trail": { endpoint: "iam/audit-log/", map: r => [new Date(r.recorded_at).toLocaleString("en-IN"), r.username || "—", r.action, `${r.entity} #${r.entity_id}`, r.summary || "—"] },
};

function ModuleView({ name, onAction, reloadKey, openAction }: { name: string; onAction: Notify; reloadKey: number; openAction: (type: string) => void }) {
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
    <div className="module-title"><div><p className="eyebrow">{data.eyebrow}</p><h2>{data.title}</h2><p>{data.blurb || "Live records from the Phloz fleet database."}</p></div>{data.action ? <button className="primary module-action" onClick={() => data.actionType ? openAction(data.actionType) : onAction(data.action.replace("+ ", "") + " opened")}>{data.action}</button> : null}</div>
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

function FleetOpsView({ name, onAction, reloadKey, openAction }: { name: string; onAction: Notify; reloadKey: number; openAction: (type: string) => void }) {
  const [records, setRecords] = useState<any[]>([]);
  const [dashboard, setDashboard] = useState<any>(null);
  const [tripDetail, setTripDetail] = useState<any>(null);
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
    try {
      await fmsRequest("trips/" + trip.id + "/" + action + "/", { method: "POST" });
      onAction(trip.number + " " + action + "ed");
      setTripDetail(null);
      load();
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Action failed"); }
  };
  // A trip has no `in_transit` action of its own - the status is set directly, which is
  // what the "in transit" column on this board needs in order to be reachable at all.
  const setTripStatus = async (trip: any, status: string) => {
    try {
      await fmsRequest(`trips/${trip.id}/`, { method: "PATCH", body: JSON.stringify({ status }) });
      onAction(`${trip.number} marked ${status.replaceAll("_", " ")}`);
      setTripDetail(null);
      load();
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Action failed", "warn"); }
  };
  const board = useDragBoard({
    "planned>dispatched": trip => tripAction(trip, "dispatch"),
    "dispatched>in_transit": trip => setTripStatus(trip, "in_transit"),
    "dispatched>closed": trip => tripAction(trip, "close"),
    "in_transit>closed": trip => tripAction(trip, "close"),
  }, onAction, (trip, status) => setTripStatus(trip, status));
  if (name === "Dispatch") return <div className="module-page"><div className="module-title"><div><p className="eyebrow">FLEET-OPS DISPATCH</p><h2>Dispatch command board</h2><p>Drag a trip between columns to progress it, or click one to open the trip sheet.</p></div><button className="primary module-action" onClick={() => openAction("trip")}>＋ Create trip</button></div>
    <div className="dispatch-board">{["planned","dispatched","in_transit","closed"].map(status => <section {...board.columnProps(status)} key={status}>
      <header><strong>{status.replaceAll("_"," ")}</strong><span>{records.filter(r => r.status === status).length}</span></header>
      {records.filter(r => r.status === status).map(trip => <article key={trip.id} {...board.cardProps(trip, setTripDetail)}>
        <b>{trip.number}</b><p>{trip.origin} → {trip.destination}</p><small>{trip.vehicle_number} · {trip.driver_name}</small>
        <div onClick={event => event.stopPropagation()}>
          {status === "planned" && <button onClick={() => tripAction(trip,"dispatch")}>Dispatch</button>}
          {status === "dispatched" && <button onClick={() => setTripStatus(trip,"in_transit")}>In transit</button>}
          {status !== "closed" && status !== "planned" && <button onClick={() => tripAction(trip,"close")}>Close trip</button>}
        </div>
      </article>)}
      {!loading && !records.some(r => r.status === status) && <div className="empty-column">Drop a card here</div>}
    </section>)}</div>
    {tripDetail && <DetailDrawer eyebrow="TRIP SHEET" title={tripDetail.number} status={tripDetail.status} onClose={() => setTripDetail(null)}
      fields={[["Route", `${tripDetail.origin} → ${tripDetail.destination}`], ["Vehicle", tripDetail.vehicle_number],
               ["Driver", tripDetail.driver_name], ["Consignments", (tripDetail.lorry_receipts || []).length],
               ["Planned departure", tripDetail.planned_departure ? new Date(tripDetail.planned_departure).toLocaleString("en-IN") : ""],
               ["Actual departure", tripDetail.actual_departure ? new Date(tripDetail.actual_departure).toLocaleString("en-IN") : ""],
               ["Arrived", tripDetail.arrival_at ? new Date(tripDetail.arrival_at).toLocaleString("en-IN") : ""],
               ["Trip advance", rupees(tripDetail.advance_amount)], ["Estimated cost", rupees(tripDetail.estimated_cost)]]}
      sections={[{ label: "GPS EVENTS", rows: (tripDetail.tracking_events || []).slice(0, 8).map((event: any) => ({
        key: String(event.id), primary: event.description || event.event_type,
        secondary: `${event.speed_kph} km/h`, meta: new Date(event.recorded_at).toLocaleString("en-IN") })) }]}
      actions={<>
        <button className="secondary" onClick={() => setTripDetail(null)}>Close</button>
        {tripDetail.status === "planned" && <button className="primary" onClick={() => tripAction(tripDetail, "dispatch")}>Dispatch trip</button>}
        {["dispatched", "in_transit"].includes(tripDetail.status) && <button className="primary" onClick={() => tripAction(tripDetail, "close")}>Close trip</button>}
      </>} />}
  </div>;
  if (name === "Tracking") { const trip=records[0]; const event=trip?.tracking_events?.[0]; return <div className="module-page"><div className="module-title"><div><p className="eyebrow">LIVE FLEET MAP</p><h2>Track fleet operations</h2><p>GPS positions, routes, geofences and automated trip events.</p></div><button className="primary module-action" onClick={load}>↻ Refresh GPS</button></div><div className="full-map-layout"><div className="operations-map"><div className="map-road r1"/><div className="map-road r2"/><div className="map-road r3"/><span className="city mumbai">Mumbai</span><span className="city pune">Pune</span><span className="map-pin start">●</span><span className="map-pin vehicle">▰</span><span className="map-pin finish">●</span><div className="map-progress"/></div><aside className="map-details"><p className="eyebrow">SELECTED TRIP</p><h3>{trip?.number || "No active trip"}</h3><p>{trip ? trip.origin + " → " + trip.destination : "Create a trip to begin tracking"}</p><div className="tracking-grid"><div><span>Vehicle</span><strong>{trip?.vehicle_number || "—"}</strong></div><div><span>Driver</span><strong>{trip?.driver_name || "—"}</strong></div><div><span>Speed</span><strong>{event?.speed_kph || 0} km/h</strong></div><div><span>Status</span><strong>{trip?.status?.replaceAll("_"," ") || "—"}</strong></div></div><div className="event-feed"><strong>Latest events</strong>{(trip?.tracking_events || []).map((e:any)=><p key={e.id}><i/>{e.description || e.event_type}<span>{new Date(e.recorded_at).toLocaleTimeString("en-IN")}</span></p>)}</div></aside></div></div>; }
  if (name === "Drivers") return <div className="module-page"><div className="module-title"><div><p className="eyebrow">DRIVER OPERATIONS</p><h2>Drivers & availability</h2><p>Licences, shifts, current status and last known location.</p></div><button className="primary module-action" onClick={() => openAction("driver")}>＋ Add driver</button></div><section className="module-table-card"><div className="table-wrap"><table><thead><tr><th>Driver</th><th>Phone</th><th>Licence</th><th>Expiry</th><th>Status</th></tr></thead><tbody>{records.map(r=><tr key={r.id}><td><strong>{r.name}</strong></td><td>{r.phone}</td><td>{r.licence_number}</td><td>{r.licence_expiry || "—"}</td><td><span className={"status "+r.status}>{r.status}</span></td></tr>)}</tbody></table></div></section></div>;
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

type Notify = (message: string, tone?: "ok" | "warn") => void;

const asList = (payload: any): any[] => (Array.isArray(payload) ? payload : payload?.results || []);
// DRF paginates at 50 by default, so `results.length` is a page size, not a total.
const asCount = (payload: any, records: any[]): number => (typeof payload?.count === "number" ? payload.count : records.length);
// Board and watchlist screens render a whole working set, so they ask for a larger page.
const wholeSet = (endpoint: string) => endpoint + (endpoint.includes("?") ? "&" : "?") + "page_size=500";
const rupees = (value: any) => "₹" + Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const orderColumns: [string, string][] = [["created", "Booked"], ["assigned", "Allocated"], ["dispatched", "Dispatched"], ["in_transit", "In transit"], ["completed", "Delivered"]];

// --- Kanban plumbing ------------------------------------------------------
// A card is dragged by id; the column it lands on decides which API action runs.
// `moves` maps "fromStatus>toStatus" to a handler, so an illegal drop simply says so
// rather than silently doing the wrong thing.
type CardMove = (card: any) => void | Promise<void>;
type CardFallback = (card: any, toStatus: string) => void | Promise<void>;

// `moves` holds the transitions that do real work - capturing an ePOD, opening the
// allocation panel, converting an indent. `fallback` handles everything else by simply
// setting the status, so any column can be dropped on.
function useDragBoard(moves: Record<string, CardMove>, onRefuse: Notify, fallback?: CardFallback) {
  const [dragging, setDragging] = useState<any>(null);
  const [over, setOver] = useState("");
  // A drag that ends where it started is a click, so suppress the click that follows.
  const suppressClick = useRef(false);

  // Pointer events rather than HTML5 drag-and-drop: no dataTransfer quirks between
  // browsers, and the same code works with a finger on a tablet in the yard.
  const startDrag = (card: any, event: React.PointerEvent) => {
    if (event.button !== 0) return;
    if ((event.target as HTMLElement).closest("button")) return;   // let card buttons work
    suppressClick.current = false;
    const startX = event.clientX;
    const startY = event.clientY;
    let moved = false;

    const columnAt = (x: number, y: number) =>
      (document.elementFromPoint(x, y) as HTMLElement | null)?.closest("[data-status]")?.getAttribute("data-status") || "";

    const onMove = (moveEvent: PointerEvent) => {
      if (!moved && Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < 6) return;
      if (!moved) {
        moved = true;
        setDragging(card);
        document.body.classList.add("dragging-card");
      }
      setOver(columnAt(moveEvent.clientX, moveEvent.clientY));
    };

    const onUp = (upEvent: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("dragging-card");
      setDragging(null);
      setOver("");
      if (!moved) return;                                          // it was a click
      suppressClick.current = true;   // cleared by the next press, not by a timer
      const status = columnAt(upEvent.clientX, upEvent.clientY);
      if (!status || status === card.status) return;
      const move = moves[`${card.status}>${status}`];
      if (move) { move(card); return; }
      if (fallback) { fallback(card, status); return; }
      onRefuse(`${card.number} cannot move from ${String(card.status).replaceAll("_", " ")} to ${status.replaceAll("_", " ")}`, "warn");
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
  };

  const cardProps = (card: any, onOpen?: (card: any) => void) => ({
    onPointerDown: (event: React.PointerEvent) => startDrag(card, event),
    onClick: () => {
      if (suppressClick.current) { suppressClick.current = false; return; }   // this click ended a drag
      if (onOpen) onOpen(card);
    },
    className: "dispatch-card" + (dragging && dragging.id === card.id ? " is-dragging" : ""),
  });

  const columnProps = (status: string) => ({
    "data-status": status,
    className: "dispatch-column"
      + (over === status && dragging && dragging.status !== status
          ? (moves[`${dragging.status}>${status}`] || fallback ? " drop-ok" : " drop-blocked") : ""),
  });

  return { dragging, cardProps, columnProps };
}

// A read-only detail drawer, used when a board card is clicked.
function DetailDrawer({ eyebrow, title, status, fields, sections, actions, onClose }: {
  eyebrow: string; title: string; status?: string;
  fields: [string, any][];
  sections?: { label: string; rows: { key: string; primary: string; secondary?: string; meta?: string }[] }[];
  actions?: React.ReactNode; onClose: () => void;
}) {
  return <div className="record-backdrop" onMouseDown={onClose}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
    <div className="record-head"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2>
      {status && <span className={"status " + String(status).replaceAll("_", "-")}>{String(status).replaceAll("_", " ")}</span>}
    </div><button className="panel-close" onClick={onClose}>×</button></div>
    <div className="record-fields">{fields.map(([label, value]) => <div className="record-field" key={label}>
      <span>{label}</span><strong>{value === null || value === undefined || value === "" ? "—" : String(value)}</strong>
    </div>)}</div>
    {(sections || []).map(section => <div className="record-timeline" key={section.label}>
      <p className="eyebrow">{section.label}</p>
      {section.rows.length ? section.rows.map(row => <div key={row.key}>
        <i /><span><strong>{row.primary}</strong>{row.secondary && <small>{row.secondary}</small>}</span>
        {row.meta && <time>{row.meta}</time>}
      </div>) : <div><i /><span><strong>Nothing yet</strong></span></div>}
    </div>)}
    {actions && <div className="record-actions">{actions}</div>}
  </aside></div>;
}

function OrdersView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
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
      // Only refresh the drawer if it was already open, and only from a response that is
      // actually an order: `activity` returns a tracking activity, not the consignment.
      setSelected((current: any) => (current && updated?.number ? updated : null));
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 90) : "Action failed");
    } finally { setBusy(false); }
  };

  // Both of these answer with something other than the order, so they refresh the drawer
  // from the consignment itself rather than from the response.
  const reopen = async (order: any) => { try { setSelected(await fmsRequest<any>(`orders/${order.id}/`)); } catch { setSelected(null); } load(); };

  const issueOtp = async (order: any) => {
    setBusy(true);
    try {
      const result = await fmsRequest<any>(`orders/${order.id}/pod-request/`, { method: "POST", body: "{}" });
      onAction(`Delivery OTP ${result.otp} issued · valid ${result.valid_hours}h`);
      await reopen(order);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not issue the OTP", "warn");
    } finally { setBusy(false); }
  };

  const raiseInvoice = async (order: any) => {
    setBusy(true);
    try {
      const result = await fmsRequest<any>(`orders/${order.id}/invoice/`, { method: "POST", body: "{}" });
      const bill = result.invoice;
      onAction(result.created
        ? `Invoice ${bill.number} raised for ${rupees(bill.total_amount)}${result.journal_entry ? ` · voucher ${result.journal_entry.number}` : ""}`
        : `Already billed on ${bill.number}`);
      if (result.ledger_error) onAction(result.ledger_error.slice(0, 120), "warn");
      await reopen(order);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not raise the invoice", "warn");
    } finally { setBusy(false); }
  };

  const totalValue = orders.reduce((sum, order) => sum + Number(order.total_amount || 0), 0);
  const active = orders.filter(order => !["completed", "cancelled"].includes(order.status));

  // Dropping onto "Allocated" opens the allocation panel, because an order cannot be
  // assigned without naming a driver and a vehicle.
  const board = useDragBoard({
    "created>assigned": order => { setSelected(order); setDriver(""); setVehicle(""); },
    "assigned>dispatched": order => run(order, "dispatch"),
    "dispatched>in_transit": order => run(order, "activity", { status: "in_transit", code: "IN_TRANSIT", details: "Moved on the dispatch board" }),
    "dispatched>completed": order => run(order, "complete", { receiver_name: "Consignee", proof_type: "signature" }),
    "in_transit>completed": order => run(order, "complete", { receiver_name: "Consignee", proof_type: "signature" }),
    "assigned>created": order => run(order, "activity", { status: "created", code: "ALLOCATION_RELEASED", details: "Returned to the booking queue" }),
  }, onAction, (order, status) => run(order, "activity", {
    status, code: "STATUS_CHANGED", details: `Moved to ${status.replaceAll("_", " ")} on the board`,
  }));

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">FLEETOPS ORDERS</p><h2>Consignment orders</h2><p>Drag a card between columns to move it on, or click one to open the consignment.</p></div><button className="primary module-action" onClick={() => openAction("order")}>＋ New order</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Total orders</span><strong>{loading ? "—" : orderTotal}</strong><small>Across all statuses</small></div>
      <div className="module-stat"><span>Active now</span><strong>{loading ? "—" : active.length}</strong><small>Booked, allocated or moving</small></div>
      <div className="module-stat"><span>Order value</span><strong>{rupees(totalValue)}</strong><small>Freight incl. GST</small></div>
    </div>
    <div className="dispatch-board">{orderColumns.map(([status, label]) => {
      const bucket = orders.filter(order => order.status === status);
      return <section {...board.columnProps(status)} key={status}>
        <header><strong>{label}</strong><span>{bucket.length}</span></header>
        {bucket.map(order => <article key={order.id}
            {...board.cardProps(order, opened => { setSelected(opened); setDriver(opened.driver || ""); setVehicle(opened.vehicle || ""); })}>
          <b>{order.number}</b>
          <p>{order.pickup_city} → {order.dropoff_city}</p>
          <small>{order.customer_name} · {rupees(order.total_amount)}</small>
          <small className="tracking-code">{order.tracking_number}</small>
          <div onClick={event => event.stopPropagation()}>
            {status === "created" && <button disabled={busy} onClick={() => { setSelected(order); setDriver(""); setVehicle(""); }}>Allocate</button>}
            {status === "assigned" && <button disabled={busy} onClick={() => run(order, "dispatch")}>Dispatch</button>}
            {["dispatched", "in_transit"].includes(status) && <button disabled={busy} onClick={() => run(order, "complete", { receiver_name: "Consignee", proof_type: "signature" })}>Deliver</button>}
          </div>
        </article>)}
        {!loading && bucket.length === 0 && <div className="empty-column">Drop a card here</div>}
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
      <div className="record-timeline"><p className="eyebrow">PROOF OF DELIVERY</p>
        {(selected.proofs || []).map((proof: any) => <div key={proof.id}><i /><span><strong>{proof.receiver_name || "Awaiting the drop"}</strong><small>{proof.status}{proof.otp ? ` · OTP ${proof.otp}` : ""}{Number(proof.shortage_kg) ? ` · ${Number(proof.shortage_kg)} kg short` : ""}{proof.damage_reported ? " · damage" : ""}</small></span><time>{proof.captured_at ? new Date(proof.captured_at).toLocaleDateString("en-IN") : "—"}</time></div>)}
        {!(selected.proofs || []).length && <div><i /><span><strong>No proof yet</strong><small>{selected.pod_required ? "Issue the delivery OTP when the truck nears the drop" : "This consignment does not need proof"}</small></span><time>—</time></div>}
        <button className="secondary full-button" disabled={busy || ["cancelled", "completed"].includes(selected.status)} onClick={() => issueOtp(selected)}>Issue delivery OTP</button>
      </div>
      <div className="record-timeline"><p className="eyebrow">TRACKING ACTIVITY</p>
        {(selected.activities || []).map((activity: any) => <div key={activity.id}><i /><span><strong>{activity.code.replaceAll("_", " ")}</strong><small>{activity.details || activity.status}{activity.city ? ` · ${activity.city}` : ""}</small></span><time>{new Date(activity.recorded_at).toLocaleString("en-IN")}</time></div>)}
      </div>
      <div className="record-actions">
        <button className="secondary" disabled={busy || selected.status === "cancelled"} onClick={() => run(selected, "cancel", { reason: "Cancelled from dispatch desk" })}>Cancel order</button>
        <button className="secondary" disabled={busy || !selected.service_rate} onClick={() => run(selected, "reprice")}>Reprice</button>
        <button className="primary" disabled={busy || selected.status !== "completed"} onClick={() => raiseInvoice(selected)}>Raise invoice</button>
      </div>
    </aside></div>}
  </div>;
}

function RatesView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
  const [rates, setRates] = useState<any[]>([]);
  const [quote, setQuote] = useState<any>(null);
  const [projection, setProjection] = useState<any>(null);
  const [mode, setMode] = useState<"freight" | "margin">("freight");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { fmsRequest<any>(wholeSet("service-rates/")).then(payload => setRates(asList(payload))).catch(() => undefined); }, [reloadKey]);

  const estimate = async (event: React.FormEvent) => {
    event.preventDefault();
    setWorking(true); setError(""); setQuote(null); setProjection(null);
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const lane = {
      service_rate: Number(form.get("service_rate")),
      distance_km: Number(form.get("distance_km") || 0), weight_kg: Number(form.get("weight_kg") || 0),
      halt_days: Number(form.get("halt_days") || 0), other_charges: Number(form.get("other_charges") || 0),
    };
    try {
      if (mode === "margin") {
        const payload = await fmsRequest<any>("service-rates/project/", { method: "POST", body: JSON.stringify({
          ...lane, trips_per_month: Number(form.get("trips_per_month") || 1),
          ...(form.get("diesel_price") ? { diesel_price: Number(form.get("diesel_price")) } : {}),
          ...(form.get("mileage_kmpl") ? { mileage_kmpl: Number(form.get("mileage_kmpl")) } : {}) }) });
        setProjection(payload); setQuote(payload.breakdown);
        onAction(`Projected margin ${payload.margin_percent}% on this lane`);
        return;
      }
      const payload = await fmsRequest<any>("service-rates/quote/", { method: "POST", body: JSON.stringify({
        ...lane, origin: String(form.get("origin") || ""), destination: String(form.get("destination") || ""),
        save_quote: form.get("save_quote") === "on" }) });
      setQuote(payload.breakdown);
      onAction(payload.quote ? `Quote ${payload.quote.number} saved` : "Freight estimated");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to price this lane");
    } finally { setWorking(false); }
  };

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">RATE MANAGEMENT</p><h2>Rate cards & freight estimator</h2><p>Per km, per ton-km, per kg and fixed lane pricing with GST, RCM and fuel surcharge.</p></div><button className="primary module-action" onClick={() => openAction("rate")}>＋ New rate card</button></div>
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
        <p className="eyebrow">{mode === "margin" ? "LANE PROJECTION" : "FREIGHT ESTIMATOR"}</p><h3>{mode === "margin" ? "Will this lane pay?" : "Price a lane"}</h3>
        <div className="mode-chips">
          <button className={mode === "freight" ? "chip active" : "chip"} onClick={() => setMode("freight")}>Freight</button>
          <button className={mode === "margin" ? "chip active" : "chip"} onClick={() => setMode("margin")}>Margin projection</button>
        </div>
        <form className="action-form quote-form" onSubmit={estimate}>
          <label>Rate card<select name="service_rate" required>{rates.map(rate => <option key={rate.id} value={rate.id}>{rate.name}</option>)}</select></label>
          <div className="form-grid">
            {mode === "freight" && <label>Origin<input name="origin" defaultValue="Bhiwandi" /></label>}
            {mode === "freight" && <label>Destination<input name="destination" defaultValue="Chakan" /></label>}
            <label>Distance (km)<input name="distance_km" type="number" step="any" defaultValue="150" /></label>
            <label>Weight (kg)<input name="weight_kg" type="number" step="any" defaultValue="12400" /></label>
            <label>Halting days<input name="halt_days" type="number" step="any" defaultValue="0" /></label>
            <label>Other charges<input name="other_charges" type="number" step="any" defaultValue="0" /></label>
            {mode === "margin" && <label>Trips a month<input name="trips_per_month" type="number" min="1" defaultValue="20" /></label>}
            {mode === "margin" && <label>Diesel ₹/litre<input name="diesel_price" type="number" step="any" placeholder="from history" /></label>}
            {mode === "margin" && <label>Mileage km/l<input name="mileage_kmpl" type="number" step="any" placeholder="from history" /></label>}
          </div>
          {mode === "freight" && <label className="checkbox-row"><input type="checkbox" name="save_quote" /> Save as a quotation</label>}
          {error && <div className="form-error">{error}</div>}
          <button className="primary full-button" disabled={working || !rates.length}>{working ? "Working…" : mode === "margin" ? "Project margin" : "Calculate freight"}</button>
        </form>
        {quote && <div className="quote-result">
          {[["Freight", quote.freight], ["Fuel surcharge", quote.fuel_surcharge], ["Loading & unloading", quote.handling_charges], ["Other charges", quote.other_charges], ["Taxable value", quote.taxable_value], [quote.reverse_charge ? "GST (reverse charge)" : `GST @ ${quote.gst_percent}%`, quote.gst_amount]].map(row => <div className="quote-line" key={String(row[0])}><span>{row[0]}</span><strong>{rupees(row[1])}</strong></div>)}
          {projection && [["Diesel for the run", projection.fuel_cost], ["On-road cash costs", projection.on_road_cost], ["Cost to run the lane", projection.total_cost]].map(row => <div className="quote-line" key={String(row[0])}><span>{row[0]}</span><strong>{rupees(row[1])}</strong></div>)}
          {projection ? <>
            <div className="invoice-total"><span>Margin a trip</span><strong>{rupees(projection.margin)}</strong><small>{projection.margin_percent}% of freight, GST excluded</small></div>
            <div className="margin-strip">
              <div><span>Revenue / km</span><strong>₹{projection.revenue_per_km}</strong></div>
              <div><span>Cost / km</span><strong>₹{projection.cost_per_km}</strong></div>
              <div><span>Break even</span><strong className={projection.revenue_per_km >= projection.break_even_rate_per_km ? "good" : "bad"}>₹{projection.break_even_rate_per_km}</strong></div>
              <div><span>Monthly revenue</span><strong>{rupees(projection.monthly.revenue)}</strong></div>
              <div><span>Monthly cost</span><strong>{rupees(projection.monthly.cost)}</strong></div>
              <div><span>Monthly margin</span><strong className={projection.monthly.margin >= 0 ? "good" : "bad"}>{rupees(projection.monthly.margin)}</strong></div>
            </div>
            <p className="basis-note">{projection.basis.from_history
              ? `Costed from the last ${projection.basis.sample_days} days: ${projection.basis.mileage_kmpl} km/l at ₹${projection.basis.diesel_price} a litre over ${Number(projection.basis.km_run).toLocaleString("en-IN")} km run, and ₹${projection.basis.on_road_cost_per_km}/km of toll, bhatta and handling.`
              : `No fuel history yet, so this uses ${projection.basis.mileage_kmpl} km/l at ₹${projection.basis.diesel_price} a litre. Record a few diesel fills and the projection costs itself.`}</p>
          </> : <div className="invoice-total"><span>Total payable</span><strong>{rupees(quote.total)}</strong><small>{quote.reverse_charge ? "GST payable by consignee under RCM" : "Inclusive of GST"}</small></div>}
        </div>}
      </aside>
    </div>
  </div>;
}

function ComplianceView({ reloadKey, openAction }: { reloadKey: number; openAction: (type: string) => void }) {
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
      <div className="module-toolbar"><div><strong>Preventive maintenance due</strong><span>{due.length} schedules</span></div><button className="chip" onClick={() => openAction("schedule")}>＋ Add service schedule</button></div>
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

const podFilters: [string, string][] = [["", "All"], ["awaiting", "Awaiting delivery"], ["submitted", "In review"],
                                        ["verified", "Verified"], ["rejected", "Rejected"]];

function EpodView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [proofs, setProofs] = useState<any[]>([]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("proofs/")).then(payload => setProofs(asList(payload)))
      .catch(() => undefined).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  // Keep the drawer looking at the record the server just returned.
  const refresh = (updated: any) => {
    setSelected((current: any) => (current && updated?.id ? updated : current));
    load();
  };

  const call = async (path: string, body: Record<string, unknown>, message: string) => {
    setBusy(true);
    try {
      const updated = await fmsRequest<any>(path, { method: "POST", body: JSON.stringify(body) });
      onAction(message);
      refresh(updated.proof || updated);
      setReason("");
      return updated;
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Action failed", "warn");
    } finally { setBusy(false); }
  };

  const issue = (proof: any) => call(`orders/${proof.order}/pod-request/`, {}, "Delivery OTP issued");
  const capture = async (event: React.FormEvent) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    await call(`orders/${selected.order}/pod-submit/`, {
      receiver_name: String(form.get("receiver_name") || ""), otp: String(form.get("otp") || ""),
      file_url: String(form.get("file_url") || ""), remarks: String(form.get("remarks") || ""),
      shortage_kg: Number(form.get("shortage_kg") || 0), damage_reported: form.get("damage_reported") === "on",
    }, "ePOD captured");
  };

  const shown = filter ? proofs.filter(proof => proof.status === filter) : proofs;
  const counts = (status: string) => proofs.filter(proof => proof.status === status).length;

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">ELECTRONIC PROOF OF DELIVERY</p><h2>ePOD</h2><p>Issue the delivery OTP, capture what the driver hands back, and clear it for billing. A consignment cannot be invoiced until its proof is verified.</p></div></div>
    <div className="module-stats">
      <div className="module-stat"><span>Awaiting delivery</span><strong>{loading ? "—" : counts("awaiting")}</strong><small>OTP issued, truck still running</small></div>
      <div className="module-stat warn"><span>In review</span><strong>{loading ? "—" : counts("submitted")}</strong><small>Held by a shortage or damage</small></div>
      <div className="module-stat"><span>Verified</span><strong>{loading ? "—" : counts("verified")}</strong><small>Cleared for invoicing</small></div>
    </div>
    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>Delivery proofs</strong><span>{shown.length} of {proofs.length}</span></div>
        <div className="toolbar-actions">{podFilters.map(([value, label]) => <button key={value} className={value === filter ? "chip active" : "chip"} onClick={() => setFilter(value)}>{label}</button>)}</div></div>
      <div className="table-wrap"><table><thead><tr><th>Consignment</th><th>Customer</th><th>Drop</th><th>Received by</th><th>Captured</th><th>Exception</th><th>Status</th></tr></thead>
        <tbody>{shown.map(proof => <tr key={proof.id} className="clickable" onClick={() => { setSelected(proof); setReason(""); }}>
          <td><strong>{proof.order_number}</strong><small>{proof.tracking_number}</small></td>
          <td>{proof.customer_name}</td>
          <td>{proof.destination || "—"}</td>
          <td>{proof.receiver_name || "—"}</td>
          <td>{proof.captured_at ? new Date(proof.captured_at).toLocaleString("en-IN") : "—"}</td>
          <td>{proof.is_clean ? "Clean" : [Number(proof.shortage_kg) ? `${Number(proof.shortage_kg)} kg short` : "", proof.damage_reported ? "damage" : ""].filter(Boolean).join(" · ")}</td>
          <td><span className={"status " + proof.status}>{proof.status}</span></td>
        </tr>)}</tbody></table></div>
      {!loading && !shown.length && <div className="data-state">No delivery proofs in this state.</div>}
    </section>

    {selected && <div className="record-backdrop" onMouseDown={() => setSelected(null)}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">ePOD {selected.tracking_number}</p><h2>{selected.order_number}</h2><span className={"status " + selected.status}>{selected.status}</span></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>
      <div className="record-fields">
        <div className="record-field"><span>Customer</span><strong>{selected.customer_name}</strong></div>
        <div className="record-field"><span>Drop</span><strong>{selected.destination || "—"}</strong></div>
        <div className="record-field"><span>Received by</span><strong>{selected.receiver_name || "—"}{selected.receiver_phone ? ` · ${selected.receiver_phone}` : ""}</strong></div>
        <div className="record-field"><span>Proof type</span><strong>{selected.proof_type}</strong></div>
        <div className="record-field"><span>Shortage</span><strong>{Number(selected.shortage_kg) ? `${Number(selected.shortage_kg)} kg` : "None"}</strong></div>
        <div className="record-field"><span>Damage</span><strong>{selected.damage_reported ? "Reported" : "None"}</strong></div>
        <div className="record-field"><span>Captured</span><strong>{selected.captured_at ? new Date(selected.captured_at).toLocaleString("en-IN") : "Not yet"}</strong></div>
        <div className="record-field"><span>Verified</span><strong>{selected.verified_at ? `${new Date(selected.verified_at).toLocaleDateString("en-IN")} · ${selected.verified_by}` : "—"}</strong></div>
      </div>
      {selected.remarks && <p className="pod-note">{selected.remarks}</p>}
      {selected.rejection_reason && <p className="pod-note warn">Rejected: {selected.rejection_reason}</p>}
      {selected.file_url && <p className="pod-note"><a href={selected.file_url} target="_blank" rel="noreferrer">Open the signed POD</a></p>}

      <div className="allocate-box">
        <p className="eyebrow">DELIVERY OTP</p>
        <div className="tracking-grid">
          <div><span>Code</span><strong className="otp-code">{selected.otp || "not issued"}</strong></div>
          <div><span>State</span><strong>{selected.otp_verified ? "Quoted back by the consignee" : selected.otp_expired ? "Expired" : selected.otp ? "Issued, awaiting the drop" : "—"}</strong></div>
        </div>
        <button className="secondary full-button" disabled={busy || selected.status === "verified"} onClick={() => issue(selected)}>{selected.otp ? "Issue a fresh OTP" : "Issue delivery OTP"}</button>
      </div>

      {["awaiting", "rejected"].includes(selected.status) && <form className="action-form pod-capture" onSubmit={capture}>
        <p className="eyebrow">RECORD THE CAPTURE</p>
        <div className="form-grid">
          <label>Received by<input name="receiver_name" defaultValue={selected.receiver_name} required /></label>
          <label>OTP quoted<input name="otp" defaultValue="" maxLength={6} /></label>
          <label>Shortage (kg)<input name="shortage_kg" type="number" step="any" defaultValue="0" /></label>
          <label>Signed POD link<input name="file_url" type="url" placeholder="https://…" /></label>
        </div>
        <label>Remarks<input name="remarks" defaultValue="" /></label>
        <label className="checkbox-row"><input type="checkbox" name="damage_reported" /> Damage reported at the drop</label>
        <button className="primary full-button" disabled={busy}>Save ePOD</button>
      </form>}

      {selected.status === "submitted" && <div className="allocate-box">
        <p className="eyebrow">OFFICE REVIEW</p>
        <label className="reject-reason">Reason, if you are sending it back<input value={reason} onChange={event => setReason(event.target.value)} placeholder="Shortage not signed by the consignee" /></label>
      </div>}
      <div className="record-actions">
        <button className="secondary" disabled={busy || !selected.captured_at || selected.status === "rejected"} onClick={() => call(`proofs/${selected.id}/reject/`, { reason }, "ePOD sent back")}>Reject</button>
        <button className="primary" disabled={busy || !selected.captured_at || selected.status === "verified"} onClick={() => call(`proofs/${selected.id}/verify/`, {}, "ePOD verified")}>Verify</button>
      </div>
    </aside></div>}
  </div>;
}

// --- Administration -------------------------------------------------------

function UsersView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const load = () => { setLoading(true); fmsRequest<any>(wholeSet("iam/users/")).then(payload => setUsers(asList(payload))).finally(() => setLoading(false)); };
  useEffect(load, [reloadKey]);
  const toggle = async (person: any) => {
    setBusy(true);
    const next = person.status === "active" ? "deactivate" : "activate";
    try { await fmsRequest(`iam/users/${person.id}/${next}/`, { method: "POST" }); onAction(`${person.username} ${next}d`); load(); }
    catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Action failed"); }
    finally { setBusy(false); }
  };
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">USER MANAGEMENT</p><h2>People & logins</h2><p>Every login carries a role and a branch. Deactivating a login blocks it immediately.</p></div><button className="primary module-action" onClick={() => openAction("user")}>＋ Add user</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Users</span><strong>{loading ? "—" : users.length}</strong><small>Across all branches</small></div>
      <div className="module-stat"><span>Active</span><strong>{loading ? "—" : users.filter(u => u.status === "active").length}</strong><small>Able to sign in</small></div>
      <div className="module-stat"><span>Without a role</span><strong>{loading ? "—" : users.filter(u => !u.role).length}</strong><small>These have unrestricted access</small></div>
    </div>
    <section className="module-table-card"><div className="table-wrap"><table>
      <thead><tr><th>User</th><th>Employee code</th><th>Role</th><th>Branch</th><th>Status</th><th>Action</th></tr></thead>
      <tbody>{users.map(person => <tr key={person.id}>
        <td><strong>{person.username}</strong><small>{[person.first_name, person.last_name].filter(Boolean).join(" ") || person.designation || "—"}</small></td>
        <td>{person.employee_code}</td>
        <td>{person.role_name || (person.is_superuser ? "Administrator" : "— unrestricted —")}</td>
        <td>{person.branch_name || "All branches"}</td>
        <td><span className={"status " + person.status}>{person.status}</span></td>
        <td><button className="row-action" disabled={busy} onClick={() => toggle(person)}>{person.status === "active" ? "Deactivate" : "Activate"}</button></td>
      </tr>)}</tbody>
    </table></div>{!loading && !users.length && <div className="data-state">No users yet.</div>}</section>
  </div>;
}

function RolesView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [roles, setRoles] = useState<any[]>([]);
  const [catalogue, setCatalogue] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const load = () => fmsRequest<any>(wholeSet("iam/roles/")).then(payload => setRoles(asList(payload))).catch(() => undefined);
  useEffect(() => { load(); fmsRequest<any>("iam/permissions/").then(payload => setCatalogue(asList(payload))).catch(() => undefined); }, [reloadKey]);
  const groups = Array.from(new Set(catalogue.map(item => item.group)));
  const open = (role: any) => { setSelected(role); setChosen(role.permissions || []); };
  const save = async () => {
    setBusy(true);
    try {
      await fmsRequest(`iam/roles/${selected.id}/`, { method: "PATCH", body: JSON.stringify({ permissions: chosen }) });
      onAction(`${selected.name} permissions updated`); setSelected(null); load();
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Could not save"); }
    finally { setBusy(false); }
  };
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">ACCESS CONTROL</p><h2>Roles & permissions</h2><p>A dispatcher should not see the ledger, and a workshop supervisor should not book freight.</p></div></div>
    <div className="role-grid">{roles.map(role => <button className="role-card" key={role.id} onClick={() => open(role)}>
      <div><strong>{role.name}</strong>{role.is_system && <em>system</em>}</div>
      <p>{role.description || "No description"}</p>
      <span>{(role.permissions || []).length} permissions · {role.user_count ?? 0} users</span>
    </button>)}</div>
    {selected && <div className="modal-backdrop" onMouseDown={() => setSelected(null)}><section className="action-panel" onMouseDown={event => event.stopPropagation()}>
      <div className="panel-head"><div><p className="eyebrow">ROLE PERMISSIONS</p><h2>{selected.name}</h2></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>
      <div className="action-form">
        {groups.map(group => <div className="permission-group" key={group}>
          <p className="eyebrow">{group}</p>
          {catalogue.filter(item => item.group === group).map(item => <label className="permission-row" key={item.code}>
            <input type="checkbox" checked={chosen.includes(item.code)} onChange={event =>
              setChosen(current => event.target.checked ? [...current, item.code] : current.filter(code => code !== item.code))} />
            <span><strong>{item.code}</strong><small>{item.label}</small></span>
          </label>)}
        </div>)}
        <div className="form-actions"><button type="button" className="secondary" onClick={() => setSelected(null)}>Cancel</button><button className="primary" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save permissions"}</button></div>
      </div>
    </section></div>}
  </div>;
}

// --- Accounting -----------------------------------------------------------

function VouchersView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [entries, setEntries] = useState<any[]>([]);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [composing, setComposing] = useState(false);
  const [lines, setLines] = useState([{ account: "", debit: "", credit: "", description: "" }]);
  const [narration, setNarration] = useState("");
  const [entryDate, setEntryDate] = useState(today());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => fmsRequest<any>(wholeSet("accounting/journal-entries/")).then(payload => setEntries(asList(payload))).catch(() => undefined);
  useEffect(() => { load(); fmsRequest<any>(wholeSet("accounting/accounts/")).then(payload => setAccounts(asList(payload))).catch(() => undefined); }, [reloadKey]);

  const totals = lines.reduce((sum, line) => ({ debit: sum.debit + Number(line.debit || 0), credit: sum.credit + Number(line.credit || 0) }), { debit: 0, credit: 0 });
  const balanced = totals.debit === totals.credit && totals.debit > 0;
  const setLine = (index: number, field: string, value: string) =>
    setLines(current => current.map((line, i) => i === index ? { ...line, [field]: value } : line));

  const save = async () => {
    setBusy(true); setError("");
    try {
      await fmsRequest("accounting/journal-entries/", { method: "POST", body: JSON.stringify({
        narration, entry_date: entryDate,
        lines: lines.filter(line => line.account).map(line => ({
          account: Number(line.account), debit: Number(line.debit || 0), credit: Number(line.credit || 0), description: line.description })) }) });
      onAction("Journal entry posted");
      setComposing(false); setLines([{ account: "", debit: "", credit: "", description: "" }]); setNarration("");
      load();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not post the entry"); }
    finally { setBusy(false); }
  };
  const reverse = async (entry: any) => {
    try { await fmsRequest(`accounting/journal-entries/${entry.id}/reverse/`, { method: "POST" }); onAction(`${entry.number} reversed`); load(); }
    catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Could not reverse"); }
  };

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">DOUBLE ENTRY</p><h2>Journal vouchers</h2><p>Every invoice, bill, payment and expense lands here as a balanced entry.</p></div><button className="primary module-action" onClick={() => setComposing(true)}>＋ New voucher</button></div>
    <section className="module-table-card"><div className="table-wrap"><table>
      <thead><tr><th>Voucher</th><th>Date</th><th>Source</th><th>Narration</th><th>Debit</th><th>Credit</th><th>Action</th></tr></thead>
      <tbody>{entries.map(entry => <tr key={entry.id}>
        <td><strong>{entry.number}</strong><small>{entry.branch_name || "—"}</small></td>
        <td>{entry.entry_date}</td>
        <td>{String(entry.source).replaceAll("_", " ")}</td>
        <td>{entry.narration}</td>
        <td>{rupees(entry.total_debit)}</td>
        <td>{rupees(entry.total_credit)}</td>
        <td>{entry.reversed_by ? <span className="status cancelled">reversed</span> : <button className="row-action" onClick={() => reverse(entry)}>Reverse</button>}</td>
      </tr>)}</tbody></table></div>
      {!entries.length && <div className="data-state">No vouchers yet.</div>}
    </section>

    {composing && <div className="modal-backdrop" onMouseDown={() => setComposing(false)}><section className="action-panel map-panel" onMouseDown={event => event.stopPropagation()}>
      <div className="panel-head"><div><p className="eyebrow">MANUAL JOURNAL</p><h2>New voucher</h2></div><button className="panel-close" onClick={() => setComposing(false)}>×</button></div>
      <div className="action-form">
        <div className="form-grid">
          <label>Date<input type="date" value={entryDate} onChange={event => setEntryDate(event.target.value)} /></label>
          <label>Narration<input value={narration} onChange={event => setNarration(event.target.value)} placeholder="What is this entry for?" /></label>
        </div>
        <div className="voucher-lines">
          <div className="voucher-head"><span>Account</span><span>Debit</span><span>Credit</span><span>Description</span></div>
          {lines.map((line, index) => <div className="voucher-row" key={index}>
            <select value={line.account} onChange={event => setLine(index, "account", event.target.value)}>
              <option value="">Select account</option>
              {accounts.filter(account => !account.is_group).map(account => <option key={account.id} value={account.id}>{account.code} {account.name}</option>)}
            </select>
            <input type="number" step="any" value={line.debit} onChange={event => setLine(index, "debit", event.target.value)} placeholder="0" />
            <input type="number" step="any" value={line.credit} onChange={event => setLine(index, "credit", event.target.value)} placeholder="0" />
            <input value={line.description} onChange={event => setLine(index, "description", event.target.value)} placeholder="Narration" />
          </div>)}
        </div>
        <button className="chip" onClick={() => setLines(current => [...current, { account: "", debit: "", credit: "", description: "" }])}>＋ Add line</button>
        <div className={balanced ? "voucher-total balanced" : "voucher-total"}>
          <span>Debits {rupees(totals.debit)}</span><span>Credits {rupees(totals.credit)}</span>
          <strong>{balanced ? "Balanced" : `Out by ${rupees(Math.abs(totals.debit - totals.credit))}`}</strong>
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions"><button type="button" className="secondary" onClick={() => setComposing(false)}>Cancel</button><button className="primary" disabled={!balanced || busy} onClick={save}>{busy ? "Posting…" : "Post voucher"}</button></div>
      </div>
    </section></div>}
  </div>;
}

function PaymentsView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [payments, setPayments] = useState<any[]>([]);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [customers, setCustomers] = useState<any[]>([]);
  const [vendors, setVendors] = useState<any[]>([]);
  const [bills, setBills] = useState<any[]>([]);
  const [composing, setComposing] = useState(false);
  const [kind, setKind] = useState("receipt");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => fmsRequest<any>(wholeSet("accounting/payments/")).then(payload => setPayments(asList(payload))).catch(() => undefined);
  useEffect(() => {
    load();
    fmsRequest<any>(wholeSet("accounting/accounts/?is_bank=true")).then(p => setAccounts(asList(p))).catch(() => undefined);
    fmsRequest<any>(wholeSet("customers/")).then(p => setCustomers(asList(p))).catch(() => undefined);
    fmsRequest<any>(wholeSet("vendors/")).then(p => setVendors(asList(p))).catch(() => undefined);
    fmsRequest<any>(wholeSet("accounting/vendor-bills/")).then(p => setBills(asList(p))).catch(() => undefined);
  }, [reloadKey]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const value = (name: string) => String(form.get(name) || "").trim();
    const body: Record<string, unknown> = {
      payment_type: kind, payment_date: value("payment_date"), amount: Number(value("amount")),
      mode: value("mode"), reference: value("reference"), narration: value("narration"),
      bank_account: Number(value("bank_account")),
    };
    if (kind === "receipt" && value("customer")) body.customer = Number(value("customer"));
    if (kind === "payment" && value("vendor")) body.vendor = Number(value("vendor"));
    if (kind === "payment" && value("bill")) body.allocations = [{ bill: Number(value("bill")), amount: Number(value("amount")) }];
    try {
      const created = await fmsRequest<any>("accounting/payments/", { method: "POST", body: JSON.stringify(body) });
      await fmsRequest(`accounting/payments/${created.id}/post_to_ledger/`, { method: "POST" }).catch(() => undefined);
      onAction(`${created.number} recorded`); setComposing(false); load();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not record the payment"); }
    finally { setBusy(false); }
  };

  const received = payments.filter(p => p.payment_type === "receipt").reduce((sum, p) => sum + Number(p.amount || 0), 0);
  const paid = payments.filter(p => p.payment_type === "payment").reduce((sum, p) => sum + Number(p.amount || 0), 0);
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">CASH & BANK</p><h2>Receipts & payments</h2><p>Money in from customers, money out to vendors and drivers, posted to the ledger.</p></div><button className="primary module-action" onClick={() => setComposing(true)}>＋ New voucher</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Received</span><strong>{rupees(received)}</strong><small>From customers</small></div>
      <div className="module-stat"><span>Paid out</span><strong>{rupees(paid)}</strong><small>To vendors and drivers</small></div>
      <div className="module-stat"><span>Net movement</span><strong>{rupees(received - paid)}</strong><small>Across all bank accounts</small></div>
    </div>
    <section className="module-table-card"><div className="table-wrap"><table>
      <thead><tr><th>Voucher</th><th>Date</th><th>Type</th><th>Party</th><th>Mode</th><th>Amount</th><th>Status</th></tr></thead>
      <tbody>{payments.map(payment => <tr key={payment.id}>
        <td><strong>{payment.number}</strong><small>{payment.reference || "—"}</small></td>
        <td>{payment.payment_date}</td>
        <td>{payment.payment_type}</td>
        <td>{payment.customer_name || payment.vendor_name || payment.driver_name || "—"}</td>
        <td>{String(payment.mode).toUpperCase()}</td>
        <td>{rupees(payment.amount)}</td>
        <td><span className={"status " + payment.status}>{payment.status}</span></td>
      </tr>)}</tbody></table></div>
      {!payments.length && <div className="data-state">No receipts or payments recorded yet.</div>}
    </section>

    {composing && <div className="modal-backdrop" onMouseDown={() => setComposing(false)}><section className="action-panel" onMouseDown={event => event.stopPropagation()}>
      <div className="panel-head"><div><p className="eyebrow">CASH & BANK</p><h2>{kind === "receipt" ? "Record a receipt" : "Record a payment"}</h2></div><button className="panel-close" onClick={() => setComposing(false)}>×</button></div>
      <form className="action-form" onSubmit={submit}>
        <div className="toolbar-actions voucher-toggle">
          <button type="button" className={kind === "receipt" ? "chip active" : "chip"} onClick={() => setKind("receipt")}>Receipt from customer</button>
          <button type="button" className={kind === "payment" ? "chip active" : "chip"} onClick={() => setKind("payment")}>Payment to vendor</button>
        </div>
        <div className="form-grid">
          {kind === "receipt"
            ? <label>Customer<select name="customer">{customers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
            : <label>Vendor<select name="vendor">{vendors.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>}
          <label>Bank / cash account<select name="bank_account" required>{accounts.map(a => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</select></label>
          <label>Amount (₹)<input name="amount" type="number" step="any" required /></label>
          <label>Date<input name="payment_date" type="date" defaultValue={today()} /></label>
          <label>Mode<select name="mode"><option value="neft">NEFT</option><option value="rtgs">RTGS</option><option value="imps">IMPS</option><option value="upi">UPI</option><option value="cheque">Cheque</option><option value="cash">Cash</option></select></label>
          <label>Reference (UTR / cheque)<input name="reference" /></label>
          {kind === "payment" && <label>Settle bill<select name="bill"><option value="">Do not allocate</option>{bills.filter(b => Number(b.balance_due) > 0).map(b => <option key={b.id} value={b.id}>{b.number} · {b.vendor_name} · ₹{b.balance_due}</option>)}</select></label>}
        </div>
        <label>Narration<textarea name="narration" /></label>
        {error && <div className="form-error">{error}</div>}
        <div className="form-actions"><button type="button" className="secondary" onClick={() => setComposing(false)}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : "Record & post"}</button></div>
      </form>
    </section></div>}
  </div>;
}

const financialReports: [string, string, string][] = [
  ["Trial balance", "accounting/reports/trial-balance/", "Every account with movement, and the balancing check"],
  ["Profit & loss", "accounting/reports/profit-and-loss/", "Income less expenses for the period"],
  ["Receivables", "accounting/reports/receivable-ageing/", "Customer outstanding by age"],
  ["Payables", "accounting/reports/payable-ageing/", "Vendor outstanding by age"],
  ["Vehicle P&L", "accounting/reports/vehicle-profitability/", "Revenue less running cost, per truck"],
  ["GST summary", "accounting/reports/gst-summary/", "Output less input GST for the period"],
];

function FinancialsView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [tab, setTab] = useState(0);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState({ from: "", to: "" });
  const load = () => {
    setLoading(true); setData(null);
    const query = range.from || range.to ? `?from=${range.from}&to=${range.to}` : "";
    fmsRequest<any>(financialReports[tab][1] + query).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  };
  useEffect(load, [tab, reloadKey]);

  const render = () => {
    if (loading) return <div className="data-state">Loading…</div>;
    if (!data) return <div className="data-state error">Could not load this report.</div>;
    if (tab === 0) return <><div className="table-wrap"><table><thead><tr><th>Code</th><th>Account</th><th>Type</th><th>Debit</th><th>Credit</th></tr></thead>
      <tbody>{(data.accounts || []).map((row: any) => <tr key={row.code}><td><strong>{row.code}</strong></td><td>{row.name}</td><td>{row.account_type}</td><td>{rupees(row.debit)}</td><td>{rupees(row.credit)}</td></tr>)}</tbody></table></div>
      <div className={data.balanced ? "voucher-total balanced" : "voucher-total"}><span>Total debit {rupees(data.total_debit)}</span><span>Total credit {rupees(data.total_credit)}</span><strong>{data.balanced ? "Balanced" : "Out of balance"}</strong></div></>;
    if (tab === 1) return <div className="pnl-grid">
      <div><p className="eyebrow">INCOME</p>{(data.income || []).map((row: any) => <div className="quote-line" key={row.code}><span>{row.name}</span><strong>{rupees(row.amount)}</strong></div>)}<div className="quote-line total"><span>Total income</span><strong>{rupees(data.total_income)}</strong></div></div>
      <div><p className="eyebrow">EXPENSES</p>{(data.expenses || []).map((row: any) => <div className="quote-line" key={row.code}><span>{row.name}</span><strong>{rupees(row.amount)}</strong></div>)}<div className="quote-line total"><span>Total expenses</span><strong>{rupees(data.total_expense)}</strong></div></div>
      <div className="invoice-total"><span>Net profit</span><strong>{rupees(data.net_profit)}</strong><small>{data.margin_percent}% margin</small></div>
    </div>;
    if (tab === 2 || tab === 3) return <><div className="table-wrap"><table><thead><tr><th>Party</th><th>Current</th><th>1-30</th><th>31-60</th><th>61-90</th><th>90+</th><th>Total</th></tr></thead>
      <tbody>{(data.parties || []).map((row: any) => <tr key={row.party}><td><strong>{row.party}</strong></td><td>{rupees(row.current)}</td><td>{rupees(row["1_30"])}</td><td>{rupees(row["31_60"])}</td><td>{rupees(row["61_90"])}</td><td>{rupees(row.over_90)}</td><td><strong>{rupees(row.total)}</strong></td></tr>)}</tbody></table></div>
      {!(data.parties || []).length && <div className="data-state">Nothing outstanding.</div>}</>;
    if (tab === 4) return <><div className="table-wrap"><table><thead><tr><th>Vehicle</th><th>Revenue</th><th>Running cost</th><th>Profit</th><th>Margin</th></tr></thead>
      <tbody>{(data.vehicles || []).map((row: any) => <tr key={row.vehicle}><td><strong>{row.vehicle}</strong></td><td>{rupees(row.revenue)}</td><td>{rupees(row.cost)}</td><td><strong>{rupees(row.profit)}</strong></td><td><span className={"status " + (row.profit >= 0 ? "active" : "expired")}>{row.margin_percent}%</span></td></tr>)}</tbody></table></div>
      {!(data.vehicles || []).length && <div className="data-state">No completed consignments in this period.</div>}</>;
    return <div className="analytics-grid">
      <div className="analytics-card"><span>Output GST</span><strong>{rupees(data.output_gst)}</strong><small>Collected on freight</small></div>
      <div className="analytics-card"><span>Input GST</span><strong>{rupees(data.input_gst)}</strong><small>Paid on purchases</small></div>
      <div className="analytics-card"><span>Net payable</span><strong>{rupees(data.net_payable)}</strong><small>Output less input</small></div>
    </div>;
  };

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">FINANCIAL REPORTING</p><h2>Books & financials</h2><p>{financialReports[tab][2]}.</p></div><button className="primary module-action" onClick={() => { onAction("Report refreshed"); load(); }}>↻ Refresh</button></div>
    <div className="report-tabs">{financialReports.map((report, index) => <button key={report[0]} className={index === tab ? "chip active" : "chip"} onClick={() => setTab(index)}>{report[0]}</button>)}</div>
    <div className="report-range">
      <label>From<input type="date" value={range.from} onChange={event => setRange({ ...range, from: event.target.value })} /></label>
      <label>To<input type="date" value={range.to} onChange={event => setRange({ ...range, to: event.target.value })} /></label>
      <button className="chip" onClick={load}>Apply</button>
    </div>
    <section className="module-table-card">{render()}</section>
  </div>;
}

// --- Operations flow ------------------------------------------------------

function IndentsView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
  const [indents, setIndents] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [drivers, setDrivers] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [vehicle, setVehicle] = useState("");
  const [driver, setDriver] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const load = () => { setLoading(true); fmsRequest<any>(wholeSet("indents/")).then(payload => setIndents(asList(payload))).finally(() => setLoading(false)); };
  useEffect(load, [reloadKey]);
  useEffect(() => {
    fmsRequest<any>(wholeSet("vehicles/?status=available")).then(p => setVehicles(asList(p))).catch(() => undefined);
    fmsRequest<any>(wholeSet("drivers/?status=available")).then(p => setDrivers(asList(p))).catch(() => undefined);
  }, [reloadKey]);

  const setIndentStatus = async (indent: any, status: string) => {
    setBusy(true);
    try {
      await fmsRequest(`indents/${indent.id}/`, { method: "PATCH", body: JSON.stringify({ status }) });
      onAction(`${indent.number} moved to ${status.replaceAll("_", " ")}`);
      setSelected(null); setDetail(null); load();
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Could not move the indent", "warn"); }
    finally { setBusy(false); }
  };
  const run = async (indent: any, path: string, body?: Record<string, unknown>) => {
    setBusy(true);
    try {
      await fmsRequest(`indents/${indent.id}/${path}/`, { method: "POST", body: JSON.stringify(body || {}) });
      onAction(`${indent.number} ${path}d`);
      setSelected(null); load();
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Action failed"); }
    finally { setBusy(false); }
  };

  const columns: [string, string][] = [["open", "Open demand"], ["allocated", "Allocated"], ["converted", "Converted"], ["cancelled", "Cancelled"]];
  // Allocating needs a truck named, so that drop opens the panel instead of guessing.
  const board = useDragBoard({
    "open>allocated": indent => { setSelected(indent); setVehicle(""); setDriver(""); },
    "allocated>converted": indent => run(indent, "convert"),
    "open>cancelled": indent => run(indent, "cancel", { reason: "Cancelled on the board" }),
    "allocated>cancelled": indent => run(indent, "cancel", { reason: "Cancelled on the board" }),
  }, onAction, (indent, status) => setIndentStatus(indent, status));
  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">OPERATIONS FLOW</p><h2>Indents & allocation</h2><p>Drag a card between columns, or click one to open it. Demand is captured, allocated to a truck, then converted.</p></div><button className="primary module-action" onClick={() => openAction("indent")}>＋ Raise indent</button></div>
    <div className="dispatch-board">{columns.map(([status, label]) => {
      const bucket = indents.filter(indent => indent.status === status);
      return <section {...board.columnProps(status)} key={status}>
        <header><strong>{label}</strong><span>{bucket.length}</span></header>
        {bucket.map(indent => <article key={indent.id} {...board.cardProps(indent, setDetail)}>
          <b>{indent.number}</b>
          <p>{indent.pickup_city} → {indent.dropoff_city}</p>
          <small>{indent.customer_name} · {indent.vehicle_type || "any vehicle"}</small>
          {indent.vehicle_number && <small className="tracking-code">{indent.vehicle_number} · {indent.driver_name}</small>}
          {indent.order_number && <small className="tracking-code">order {indent.order_number}</small>}
          <div onClick={event => event.stopPropagation()}>
            {status === "open" && <button onClick={() => { setSelected(indent); setVehicle(""); setDriver(""); }}>Allocate</button>}
            {status === "allocated" && <button disabled={busy} onClick={() => run(indent, "convert")}>Convert to order</button>}
            {status !== "converted" && status !== "cancelled" && <button disabled={busy} onClick={() => run(indent, "cancel", { reason: "Cancelled at the desk" })}>Cancel</button>}
          </div>
        </article>)}
        {!loading && !bucket.length && <div className="empty-column">Drop a card here</div>}
      </section>;
    })}</div>

    {detail && <DetailDrawer eyebrow="INDENT" title={detail.number} status={detail.status} onClose={() => setDetail(null)}
      fields={[["Customer", detail.customer_name], ["Branch", detail.branch_name],
               ["Loading point", detail.pickup_city], ["Unloading point", detail.dropoff_city],
               ["Vehicle required", detail.vehicle_type], ["How many", detail.vehicles_required],
               ["Material", detail.material], ["Weight", `${Number(detail.weight_kg).toLocaleString("en-IN")} kg`],
               ["Expected freight", rupees(detail.expected_rate)],
               ["Required by", detail.required_at ? new Date(detail.required_at).toLocaleString("en-IN") : ""],
               ["Allocated truck", detail.vehicle_number], ["Driver", detail.driver_name],
               ["Order", detail.order_number], ["Remarks", detail.remarks]]}
      actions={<>
        <button className="secondary" onClick={() => setDetail(null)}>Close</button>
        {detail.status === "open" && <button className="primary" onClick={() => { setSelected(detail); setDetail(null); setVehicle(""); setDriver(""); }}>Allocate a truck</button>}
        {detail.status === "allocated" && <button className="primary" disabled={busy} onClick={() => { setDetail(null); run(detail, "convert"); }}>Convert to order</button>}
      </>} />}

    {selected && <div className="modal-backdrop" onMouseDown={() => setSelected(null)}><section className="action-panel" onMouseDown={event => event.stopPropagation()}>
      <div className="panel-head"><div><p className="eyebrow">ALLOCATE A TRUCK</p><h2>{selected.number}</h2></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>
      <div className="action-form">
        <div className="tracking-grid">
          <div><span>Customer</span><strong>{selected.customer_name}</strong></div>
          <div><span>Lane</span><strong>{selected.pickup_city} → {selected.dropoff_city}</strong></div>
          <div><span>Material</span><strong>{selected.material || "—"}</strong></div>
          <div><span>Weight</span><strong>{Number(selected.weight_kg).toLocaleString("en-IN")} kg</strong></div>
        </div>
        <div className="form-grid">
          <label>Vehicle<select value={vehicle} onChange={event => setVehicle(event.target.value)}><option value="">Select an available vehicle</option>{vehicles.map(v => <option key={v.id} value={v.id}>{v.registration_number} · {v.vehicle_type}</option>)}</select></label>
          <label>Driver<select value={driver} onChange={event => setDriver(event.target.value)}><option value="">Select an available driver</option>{drivers.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        </div>
        <div className="form-actions"><button type="button" className="secondary" onClick={() => setSelected(null)}>Cancel</button><button className="primary" disabled={busy || !vehicle || !driver} onClick={() => run(selected, "allocate", { vehicle: Number(vehicle), driver: Number(driver) })}>Allocate truck</button></div>
      </div>
    </section></div>}
  </div>;
}

export default function Home() {
  const [active, setActive] = useState("Overview");
  const [toast, setToast] = useState<{ text: string; tone: string } | null>(null);
  const [action, setAction] = useState("");
  const [authenticated, setAuthenticated] = useState(false);
  const [dataVersion, setDataVersion] = useState(0);
  const [dashboard, setDashboard] = useState<any>(null);
  const [me, setMe] = useState<any>(null);
  useEffect(() => { setAuthenticated(Boolean(sessionStorage.getItem("fms_token"))); }, []);
  useEffect(() => {
    const ended = () => { setAuthenticated(false); setMe(null); };
    window.addEventListener(UNAUTHORISED_EVENT, ended);
    return () => window.removeEventListener(UNAUTHORISED_EVENT, ended);
  }, []);
  useEffect(() => {
    if (!authenticated) return;
    fmsRequest<any>("iam/me/").then(setMe).catch(() => undefined);
  }, [authenticated]);

  const signOut = () => {
    logout();
    setAuthenticated(false);
    setMe(null);
    setActive("Overview");
    setDashboard(null);
    setAction("");
  };
  useEffect(() => {
    if (!authenticated) return;
    fmsRequest<any>("dashboard/").then(setDashboard).catch(() => undefined);
  }, [authenticated, dataVersion]);

  const show: Notify = (message, tone = "ok") => {
    setToast({ text: message, tone });
    window.setTimeout(() => setToast(null), tone === "warn" ? 3600 : 2200);
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
          <div className="user">
            <span className="avatar">{initials(me?.full_name || me?.username || "")}</span>
            <div><strong>{me?.full_name || me?.username || "Signed in"}</strong><small>{me?.role || me?.designation || "Fleet operations"}</small></div>
            <button className="sign-out" onClick={signOut} title="Sign out" aria-label="Sign out">⏻</button>
          </div>
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
        </div> : active === "Modules" ? <FeatureHub onAction={show} /> : active === "Orders" ? <OrdersView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Rates" ? <RatesView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Compliance" ? <ComplianceView reloadKey={dataVersion} openAction={setAction} /> : active === "ePOD" ? <EpodView reloadKey={dataVersion} onAction={show} /> : active === "Indents" ? <IndentsView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Users" ? <UsersView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Roles" ? <RolesView reloadKey={dataVersion} onAction={show} /> : active === "Vouchers" ? <VouchersView reloadKey={dataVersion} onAction={show} /> : active === "Payments" ? <PaymentsView reloadKey={dataVersion} onAction={show} /> : active === "Financials" ? <FinancialsView reloadKey={dataVersion} onAction={show} /> : fleetOpsPages.includes(active) ? <FleetOpsView name={active} reloadKey={dataVersion} onAction={show} openAction={setAction} /> : <ModuleView name={active as keyof typeof modules} reloadKey={dataVersion} onAction={show} openAction={setAction} />}
      </section>
      {action && <ActionPanel type={action} onClose={() => setAction("")} onCreated={() => setDataVersion(v => v + 1)} onDone={(message) => { show(message); if (action !== "tracking") setAction(""); }} />}
      {toast && <div className={"toast " + toast.tone}>{toast.tone === "warn" ? "⚠" : "✓"} {toast.text}</div>}
    </main>
  );
}

