"use client";

import { useEffect, useRef, useState } from "react";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import { UNAUTHORISED_EVENT, fmsRequest, fmsRequestRaw, login, logout } from "./lib/fms-api";

const navGroups: { label: string; items: [string, string][] }[] = [
  { label: "WORKSPACE", items: [["Overview", "⌂"], ["Analytics", "◎"]] },
  { label: "TRANSPORT", items: [["Indents", "◰"], ["Dispatch", "▦"], ["Planning", "⛁"], ["Scenario Profiles", "⚙"], ["Orders", "◈"], ["Hires", "⚑"], ["ePOD", "✍"], ["Tracking", "⌖"], ["Operations", "▤"]] },
  { label: "COMMERCIAL", items: [["Customers", "◇"], ["Sales", "↗"], ["Rates", "⚖"], ["Invoices", "▥"]] },
  { label: "FLEET", items: [["Fleet", "▱"], ["Fleets", "▩"], ["Drivers", "♙"], ["Maintenance", "⚒"], ["Compliance", "▣"], ["Fuel", "⛽"], ["Issues", "⚠"]] },
  { label: "NETWORK", items: [["Vendors", "⌸"], ["Places", "⌂"], ["Service areas", "◫"], ["Zones", "◍"]] },
  { label: "FINANCE", items: [["Expenses", "▤"], ["Settlements", "₹"]] },
  { label: "ACCOUNTS", items: [["Ledger", "▦"], ["Vouchers", "▤"], ["Vendor bills", "◳"], ["Payments", "⇄"], ["Financials", "◫"]] },
  { label: "ADMIN", items: [["Users", "♟"], ["Roles", "⚿"], ["Branches", "⌸"], ["Audit trail", "◷"]] },
  { label: "PLATFORM", items: [["Modules", "⊞"]] },
  { label: "REPORTS", items: [["Driver & Availability", "♙"], ["Fleet Report", "▱"], ["Customer Invoices", "▥"], ["Vehicle Settlement", "₹"], ["Sales Report", "↗"]] },
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
  Settlements: {
    eyebrow: "DRIVER ACCOUNTS", title: "Driver settlements", action: "+ New settlement", actionType: "settlement",
    stats: [["Pending settlement", "₹1.84L", "Across 8 drivers"], ["Trip advances", "₹96,000", "11 open advances"], ["Settled this month", "₹7.2L", "42 settlements"]],
    columns: ["Driver", "Trip sheet", "Advance", "Expenses", "Status"],
    rows: [["Ramesh Yadav", "TS-2841", "₹12,000", "₹18,450", "₹6,450 due"], ["Sandeep Kumar", "TS-2839", "₹10,000", "₹9,240", "₹760 recover"], ["Vijay Raj", "TS-2834", "₹15,000", "₹21,180", "₹6,180 due"], ["Irfan Sheikh", "TS-2836", "₹8,000", "₹11,620", "₹3,620 due"]]
  },
  Invoices: {
    eyebrow: "BILLING & COLLECTIONS", title: "Customer invoices", action: "+ Generate invoice", actionType: "invoice",
    stats: [["Unbilled trips", "₹3.2L", "7 PODs received"], ["Outstanding", "₹12.8L", "₹5.6L overdue"], ["Collected this month", "₹18.6L", "92% of target"]],
    blurb: "Raised from the consignment, so freight and GST always match the rate card. Generate one here, or from an order's drawer on the Orders board.",
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

type FormField = { name: string; label: string; type?: "text" | "number" | "date" | "datetime" | "select" | "textarea"; options?: [string, string][]; source?: string; value?: string; required?: boolean; multiple?: boolean; tab?: string };
type FormSpec = { eyebrow: string; title: string; button: string; endpoint: string; fields: FormField[]; reference: (values: Record<string, string>, created: any) => string; tabs?: string[]; tabDescriptions?: Record<string, string> };

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
    tabs: ["KYC", "Billing party", "Address", "Preferences", "Vendor vehicle terms"],
    tabDescriptions: {
      "KYC": "Legal identity and credit approval for the customer account.",
      "Billing party": "The party and contact who should receive billing communication.",
      "Address": "Billing address already used for customer invoices.",
      "Preferences": "Contracted POD, invoice delivery, temperature service and payment terms.",
      "Vendor vehicle terms": "When a vendor vehicle serves this customer, keep the commercial KYC here and release the agreed advance after loading.",
    },
    fields: [
      { name: "name", label: "Customer name", required: true, tab: "KYC" },
      { name: "gstin", label: "GSTIN", required: true, tab: "KYC" },
      { name: "pan", label: "PAN", tab: "KYC" },
      { name: "phone", label: "Primary phone", tab: "KYC" },
      { name: "email", label: "Primary email", tab: "KYC" },
      { name: "credit_limit", label: "Credit limit (₹)", type: "number", value: "500000", tab: "KYC" },
      { name: "kyc_status", label: "KYC status", type: "select", options: [["pending", "Pending verification"], ["verified", "Verified"], ["rejected", "Rejected"]], tab: "KYC" },
      { name: "billing_party_name", label: "Billing party name", tab: "Billing party" },
      { name: "billing_contact_name", label: "Contact person", tab: "Billing party" },
      { name: "billing_contact_phone", label: "Contact phone", tab: "Billing party" },
      { name: "billing_contact_email", label: "Contact email", tab: "Billing party" },
      { name: "billing_address", label: "Billing address", type: "textarea", tab: "Address" },
      { name: "pod_preference", label: "POD requirement", type: "select", options: [["epod", "ePOD"], ["physical", "Physical POD"], ["both", "ePOD and physical POD"]], tab: "Preferences" },
      { name: "invoice_delivery_preference", label: "Invoice delivery", type: "select", options: [["soft_copy", "Soft copy"], ["hard_copy", "Hard copy"], ["both", "Soft and hard copy"]], tab: "Preferences" },
      { name: "customer_type", label: "Customer type", type: "select", options: [["dry", "Dry"], ["reefer", "Reefer"], ["both", "Dry and reefer"]], tab: "Preferences" },
      { name: "payment_terms_days", label: "Payment terms", type: "select", options: [["30", "30 days"], ["45", "45 days"], ["90", "90 days"]], tab: "Preferences" },
      { name: "vendor_vehicle_advance_percent", label: "Vendor advance (%)", type: "number", value: "90", tab: "Vendor vehicle terms" },
      { name: "vendor_vehicle_advance_trigger", label: "Advance release", type: "select", options: [["after_loading", "After vehicle loading"]], tab: "Vendor vehicle terms" },
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
      { name: "ownership", label: "Ownership", type: "select", options: [["own", "Own vehicle"], ["attached", "Attached vehicle"], ["leased", "Leased"], ["outside", "Outside-sourced (vendor hire)"]] },
      { name: "vendor", label: "Vendor (if attached / outside-sourced)", type: "select", source: "vendors/" },
      { name: "status", label: "Status", type: "select", options: [["available", "Available"], ["idle", "Idle"], ["on_trip", "On trip"], ["under_maintenance", "In workshop"], ["breakdown", "Breakdown"], ["inactive", "Inactive"]] },
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
      { name: "indent_type", label: "Indent type", type: "select", options: [["part_load", "Part load"], ["ftl", "Full truck load (FTL)"]] },
      { name: "delivery_access", label: "Delivery access", type: "select", options: [["no_entry_area", "No-entry area"], ["no_restriction", "Without no-entry restriction"]] },
      { name: "temperature_class", label: "Temperature", type: "select", options: [["dry", "Dry"], ["chiller", "Chiller"], ["frozen", "Frozen"], ["multi", "Multi-compartment"]] },
      { name: "temp_min_c", label: "Minimum temperature (°C)", type: "number", value: "2" },
      { name: "temp_max_c", label: "Maximum temperature (°C)", type: "number", value: "8" },
      { name: "temp_tolerance_c", label: "Temperature tolerance (±°C)", type: "number", value: "1" },
      { name: "priority", label: "Priority", type: "select", options: [["normal", "Normal"], ["urgent", "Urgent - cannot be outsourced away"], ["low", "Low - can be deferred"]] },
      { name: "required_at", label: "Required by", type: "datetime" },
      { name: "expected_delivery_at", label: "Expected delivery", type: "datetime" },
      { name: "expected_running_km", label: "Expected running (km)", type: "number", value: "0" },
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
      { name: "temperature_class", label: "Temperature", type: "select", options: [["dry", "Dry"], ["chiller", "Chiller"], ["frozen", "Frozen"], ["multi", "Multi-compartment"]] },
      { name: "priority", label: "Priority", type: "select", options: [["normal", "Normal"], ["urgent", "Urgent - cannot be outsourced away"], ["low", "Low - can be deferred"]] },
      { name: "distance_km", label: "Distance (km)", type: "number" },
      { name: "declared_value", label: "Declared value (₹)", type: "number" },
      { name: "eway_bill_number", label: "E-way bill" },
      { name: "payment_mode", label: "Freight terms", type: "select", options: [["to_pay", "To pay"], ["paid", "Paid"], ["tbb", "To be billed"], ["cod", "Cash on delivery"]] },
      { name: "special_instructions", label: "Special instructions", type: "textarea" },
    ],
  },
  hire: {
    eyebrow: "VENDOR HIRE", title: "Record a vehicle hire", button: "Save hire", endpoint: "hires/",
    reference: (_values, created) => created?.vendor_name ? `Hire from ${created.vendor_name}` : "Hire",
    fields: [
      { name: "order", label: "Order", type: "select", source: "orders/", required: true },
      { name: "vendor", label: "Vendor", type: "select", source: "vendors/", required: true },
      { name: "hire_type", label: "Hire type", type: "select", options: [["spot", "Spot hire"], ["contract", "Contract rate"]] },
      { name: "outside_vehicle_number", label: "Vehicle number" },
      { name: "outside_vehicle_type", label: "Vehicle type" },
      { name: "outside_capacity_kg", label: "Capacity (kg)", type: "number" },
      { name: "driver_name", label: "Driver name" },
      { name: "driver_phone", label: "Driver phone" },
      { name: "driver_licence", label: "Driver licence" },
      { name: "agreed_rate", label: "Agreed rate (₹)", type: "number", required: true },
      { name: "rate_basis", label: "Rate basis", type: "select", options: [["trip", "Per trip"], ["km", "Per km"], ["day", "Per day"], ["ton", "Per ton"], ["other", "Other"]] },
      { name: "loading_charge", label: "Loading (₹)", type: "number", value: "0" },
      { name: "unloading_charge", label: "Unloading (₹)", type: "number", value: "0" },
      { name: "detention_rate_per_day", label: "Detention per day (₹)", type: "number", value: "0" },
      { name: "toll_responsibility", label: "Toll paid by", type: "select", options: [["vendor", "Vendor"], ["ours", "This fleet"]] },
      { name: "advance_amount", label: "Advance paid (₹)", type: "number", value: "0" },
      { name: "payment_terms_days", label: "Payment terms (days)", type: "number", value: "30" },
      { name: "remarks", label: "Remarks", type: "textarea" },
    ],
  },
  "vendor-lane-rate": {
    eyebrow: "LANE PRICING", title: "Record a vendor's lane rate", button: "Save lane rate", endpoint: "vendor-lane-rates/",
    reference: (_values, created) => created ? `${created.origin_city} → ${created.destination_city}` : "Lane rate",
    fields: [
      { name: "vendor", label: "Vendor", type: "select", source: "vendors/", required: true },
      { name: "origin_city", label: "Origin city", required: true },
      { name: "destination_city", label: "Destination city", required: true },
      { name: "vehicle_type", label: "Vehicle type (blank = any)" },
      { name: "temperature_class", label: "Temperature", type: "select", options: [["dry", "Dry"], ["chiller", "Chiller"], ["frozen", "Frozen"], ["multi", "Multi-compartment"]] },
      { name: "rate", label: "Rate (₹)", type: "number", required: true },
      { name: "rate_basis", label: "Rate basis", type: "select", options: [["trip", "Per trip"], ["km", "Per km"], ["day", "Per day"], ["ton", "Per ton"], ["other", "Other"]] },
      { name: "valid_from", label: "Valid from", type: "date" },
      { name: "valid_until", label: "Valid until", type: "date" },
      { name: "remarks", label: "Remarks", type: "textarea" },
    ],
  },
};

// Edit reuses the same field spec as creation. A field's current value on the record beats
// the spec's default, but only once every `source` dropdown has loaded - an uncontrolled
// <select>'s defaultValue only ever applies on its first render, so if the options for a
// vehicle/customer/etc. dropdown arrive after the field has already mounted empty, the
// record's real value would never visibly get selected.
function RecordForm({ spec, record, onClose, onSaved }: { spec: FormSpec; record?: any; onClose: () => void; onSaved: (reference: string, saved: any) => void }) {
  const [options, setOptions] = useState<Record<string, any[]>>({});
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [activeTab, setActiveTab] = useState(spec.tabs?.[0] || "");
  const sources = Array.from(new Set(spec.fields.map(field => field.source).filter(Boolean) as string[]));
  useEffect(() => {
    setOptions({});
    setActiveTab(spec.tabs?.[0] || "");
    sources.forEach(source => {
      fmsRequest<any>(wholeSet(source)).then(payload => {
        setOptions(current => ({ ...current, [source]: asList(payload) }));
      }).catch(() => undefined);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec]);
  const ready = sources.every(source => Boolean(options[source]));

  const defaultFor = (field: FormField): string => {
    const raw = record ? record[field.name] : undefined;
    if (raw === undefined || raw === null) return field.value || (field.type === "select" && !field.source ? (field.options || [["", ""]])[0][0] : "");
    if (field.type === "datetime") return String(raw).slice(0, 16);
    return String(raw);
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setWorking(true); setError("");
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const missing = spec.fields.find(field => field.required && !String(form.get(field.name) ?? "").trim());
    if (missing) {
      setActiveTab(missing.tab || spec.tabs?.[0] || "");
      setError(`${missing.label} is required`);
      setWorking(false);
      return;
    }
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
      const endpoint = record ? `${spec.endpoint}${record.id}/` : spec.endpoint;
      const saved = await fmsRequest<any>(endpoint, { method: record ? "PATCH" : "POST", body: JSON.stringify(payload) });
      onSaved(spec.reference(values, saved), saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to save record");
    } finally { setWorking(false); }
  };

  if (!ready) return <div className="action-form"><div className="data-state">Loading form…</div></div>;

  const fieldsForTab = (tab: string) => spec.fields.filter(field => (field.tab || spec.tabs?.[0]) === tab);
  const renderFields = (fields: FormField[]) => <>
    <div className="form-grid">{fields.filter(field => field.type !== "textarea").map(field => <label key={field.name}>{field.label}
      {field.source ? <select name={field.name} required={field.required && !spec.tabs?.length} multiple={field.multiple} defaultValue={field.multiple ? (record?.[field.name] || []).map(String) : defaultFor(field)}>
        {!field.multiple && <option value="">{(options[field.source] || []).length ? "Select…" : "Loading…"}</option>}
        {(options[field.source] || []).map(option => <option key={option.id} value={option.id}>{sourceLabel[field.source!] ? sourceLabel[field.source!](option) : option.name}</option>)}
      </select>
      : field.type === "select" ? <select name={field.name} defaultValue={defaultFor(field)}>{(field.options || []).map(option => <option key={option[0]} value={option[0]}>{option[1]}</option>)}</select>
      : <input name={field.name} type={field.type === "number" ? "number" : field.type === "date" ? "date" : field.type === "datetime" ? "datetime-local" : "text"} step={field.type === "number" ? "any" : undefined} defaultValue={defaultFor(field)} required={field.required && !spec.tabs?.length} />}
    </label>)}</div>
    {fields.filter(field => field.type === "textarea").map(field => <label key={field.name}>{field.label}<textarea name={field.name} defaultValue={defaultFor(field)} /></label>)}
  </>;

  return <form className="action-form" onSubmit={submit}>
    {spec.tabs?.length ? <>
      <div className="form-tabs" role="tablist" aria-label={`${spec.title} sections`}>{spec.tabs.map(tab => <button key={tab} type="button" role="tab" aria-selected={activeTab === tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>{tab}</button>)}</div>
      {spec.tabs.map(tab => <section key={tab} className="form-tab-panel" role="tabpanel" hidden={activeTab !== tab}>
        {spec.tabDescriptions?.[tab] && <p className="form-tab-description">{spec.tabDescriptions[tab]}</p>}
        {renderFields(fieldsForTab(tab))}
      </section>)}
    </> : renderFields(spec.fields)}
    {error && <div className="form-error">{error}</div>}
    <div className="form-actions"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" type="submit" disabled={working}>{working ? "Saving…" : record ? "Save changes" : spec.button}</button></div>
  </form>;
}

// Billing starts from a delivered consignment, never from typed figures. The
// freight, GST and total shown here are what `price_from_rate_card` produced on
// the server, and the same call recomputes them when the invoice is raised - so
// an invoice cannot disagree with the rate card it came from.
function InvoiceFromOrderForm({ onClose, onSaved }: { onClose: () => void; onSaved: (reference: string) => void }) {
  const [orders, setOrders] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState("0");
  const [dueDays, setDueDays] = useState("15");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fmsRequest<any>("orders/billable/")
      .then(payload => setOrders(payload.orders || []))
      .catch(e => setError(e instanceof Error ? e.message.slice(0, 160) : "Could not load billable consignments"))
      .finally(() => setLoading(false));
  }, []);

  const needle = query.trim().toLowerCase();
  const shown = orders.filter(order =>
    !needle || `${order.number} ${order.customer_name} ${order.route}`.toLowerCase().includes(needle));
  const selected = orders.find(order => order.id === selectedId) || null;
  const breakdown = selected?.breakdown;
  const total = Number(selected?.total_amount || 0) + Number(extra || 0);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const payload = await fmsRequest<any>(`orders/${selected.id}/invoice/`, {
        method: "POST",
        body: JSON.stringify({ additional_charges: Number(extra || 0), due_days: Number(dueDays || 0) }),
      });
      onSaved(payload.invoice?.number || payload.number || "Invoice raised");
    } catch (e) {
      setError(e instanceof Error ? e.message.slice(0, 220) : "Could not raise the invoice");
    } finally { setBusy(false); }
  };

  return <form className="action-form" onSubmit={submit}>
    {error && <div className="login-error">{error}</div>}

    <p className="eyebrow">DELIVERED CONSIGNMENT</p>
    <input value={query} onChange={event => setQuery(event.target.value)}
           placeholder="Search consignment, customer or lane…" aria-label="Search consignments" />

    <div className="shipment-rows" style={{ maxHeight: 280, overflowY: "auto", margin: "8px 0 4px" }}>
      {shown.map(order => <div key={order.id}
        className={"shipment-row" + (order.id === selectedId ? " active" : "")}
        style={order.blocked_reason ? { opacity: 0.55, cursor: "not-allowed" } : undefined}
        onClick={() => !order.blocked_reason && setSelectedId(order.id)}>
        <div style={{ minWidth: 0 }}>
          <strong>{order.number}</strong>
          <small>{order.customer_name} · {order.route}{order.rate_card ? ` · ${order.rate_card}` : ""}</small>
        </div>
        <div className="shipment-row-meta">
          {order.blocked_reason
            ? <span className="status cancelled">{order.blocked_reason}</span>
            : <strong>{rupees(order.total_amount)}</strong>}
        </div>
      </div>)}
      {!loading && !shown.length && <div className="data-state">
        {orders.length ? "No consignment matches that search." : "No delivered consignment is waiting to be billed."}
      </div>}
      {loading && <div className="data-state">Loading billable consignments…</div>}
    </div>

    {selected && <>
      {breakdown ? <div className="tracking-grid">
        <div><span>Freight</span><strong>{rupees(breakdown.freight)}</strong></div>
        <div><span>Fuel surcharge</span><strong>{rupees(breakdown.fuel_surcharge)}</strong></div>
        <div><span>Handling</span><strong>{rupees(breakdown.handling_charges)}</strong></div>
        <div><span>Taxable value</span><strong>{rupees(breakdown.taxable_value)}</strong></div>
        <div><span>GST {breakdown.gst_percent}%</span>
             <strong>{breakdown.reverse_charge ? "RCM — payable by consignee" : rupees(breakdown.gst_amount)}</strong></div>
        <div><span>Distance · weight</span>
             <strong>{Number(selected.distance_km).toFixed(0)} km · {Number(selected.weight_kg).toLocaleString("en-IN")} kg</strong></div>
      </div> : <p className="pod-note warn">This consignment has no rate card, so the amount below is whatever freight was recorded against it.</p>}

      <div className="form-grid">
        <label>Additional charges (₹)
          <input type="number" step="any" value={extra} onChange={event => setExtra(event.target.value)} /></label>
        <label>Payment due in (days)
          <input type="number" min="0" value={dueDays} onChange={event => setDueDays(event.target.value)} /></label>
      </div>

      <div className="margin-strip">
        <div><span>Rate card total</span><strong>{rupees(selected.total_amount)}</strong></div>
        <div><span>Additional</span><strong>{rupees(extra)}</strong></div>
        <div><span>Invoice total</span><strong className="good">{rupees(total)}</strong></div>
      </div>
    </>}

    <div className="record-actions">
      <button type="button" className="secondary" onClick={onClose}>Cancel</button>
      <button className="primary" disabled={busy || !selected}>
        {busy ? "Raising…" : "Raise invoice"}
      </button>
    </div>
  </form>;
}

function ActionPanel({ type, onClose, onCreated }: { type: string; onClose: () => void; onCreated: () => void }) {
  const [complete, setComplete] = useState(false);
  const [reference, setReference] = useState("");
  // Billing is the one action that cannot be a plain record form: an invoice has
  // to be raised against a consignment so it prices itself from the rate card.
  const billing = type === "invoice";
  const spec = recordForms[type];
  if (!billing && !spec) return null;
  const eyebrow = billing ? "FREIGHT BILLING" : spec.eyebrow;
  const title = billing ? "Bill a delivered consignment" : spec.title;
  const saved = (newReference: string) => { setReference(newReference); setComplete(true); onCreated(); };

  return <div className="modal-backdrop" onMouseDown={onClose}><section className="action-panel" onMouseDown={event => event.stopPropagation()}>
    <div className="panel-head"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div><button className="panel-close" onClick={onClose}>×</button></div>
    {complete ? <div className="success-state"><span>✓</span><h3>{reference}</h3><p>Saved to the live fleet database. The module list has been refreshed.</p><div className="success-actions"><button className="secondary" onClick={onClose}>Close</button><button className="primary" onClick={() => { setComplete(false); setReference(""); }}>Add another</button></div></div>
      : billing ? <InvoiceFromOrderForm onClose={onClose} onSaved={saved} />
      : <RecordForm spec={spec} onClose={onClose} onSaved={saved} />}
  </section></div>;
}

const liveModules: Record<string, { endpoint: string; map: (record: any) => string[] }> = {
  Customers: { endpoint: "customers/", map: r => [r.name, r.gstin, "₹" + Number(r.credit_limit).toLocaleString("en-IN"), r.email || "—", r.kyc_status] },
  Sales: { endpoint: "quotes/", map: r => [r.number, r.customer_name, r.origin + " → " + r.destination, "₹" + Number(r.freight_amount).toLocaleString("en-IN"), r.status] },
  Operations: { endpoint: "lorry-receipts/", map: r => [r.number, r.consignor + " → " + r.consignee, r.origin + " → " + r.destination, r.eway_bill_number || "—", r.status] },
  Settlements: { endpoint: "settlements/", map: r => [r.driver_name, "Trip #" + r.trip, "₹" + Number(r.advance_amount).toLocaleString("en-IN"), "₹" + Number(r.approved_expenses).toLocaleString("en-IN"), r.status] },
  Invoices: { endpoint: "invoices/", map: r => [r.number, r.customer_name, r.order_number || r.trip_number || "manual", r.due_date, rupees(r.total_amount), r.status] },
  Vendors: { endpoint: "vendors/", map: r => [r.name, r.vendor_type, r.city || "—", r.gstin || "—", r.status] },
  Places: { endpoint: "places/", map: r => [r.name, r.place_type, r.city, r.pincode || "—", r.status] },
  "Service areas": { endpoint: "service-areas/", map: r => [r.name, r.code, r.states || "—", String(r.zone_count ?? 0), r.status] },
  Zones: { endpoint: "zones/", map: r => [r.name, r.service_area_name, Number(r.center_latitude).toFixed(3) + ", " + Number(r.center_longitude).toFixed(3), r.radius_km + " km", r.zone_type] },
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
  const feed = liveModules[name];
  const [query, setQuery] = useState("");
  const [records, setRecords] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const editSpec = data?.actionType ? recordForms[data.actionType] : undefined;

  const load = () => {
    if (!feed) { setLoading(false); return; }
    let active = true; setLoading(true); setLoadError("");
    fmsRequest<any>(wholeSet(feed.endpoint)).then(payload => {
      if (!active) return;
      const items = asList(payload);
      setRecords(items);
      setTotal(asCount(payload, items));
    }).catch(error => { if (active) setLoadError(error instanceof Error ? error.message : "Unable to load records"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  };
  useEffect(load, [name, reloadKey]);

  // ModuleView is the catch-all for any sidebar entry without its own branch,
  // so a screen added to `navGroups` whose route was never wired up lands here
  // with no spec at all. Say so plainly rather than taking the whole page down
  // on `undefined.actionType` (which is exactly what a dropped route did once).
  if (!data || !feed) return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">NOT WIRED UP</p><h2>{name}</h2>
      <p>This screen is listed in the sidebar but has no view connected to it yet.</p></div></div>
  </div>;

  const rows = records.map(record => ({ record, cells: feed.map(record) }));
  const visibleRows = rows.filter(row => row.cells.join(" ").toLowerCase().includes(query.toLowerCase()));
  const selected = records.find(record => record.id === selectedId) || null;
  const selectedCells = selected ? feed.map(selected) : null;

  const closeDrawer = () => { setSelectedId(null); setEditing(false); };

  // The last cell of every module row is rendered as a status pill. A record
  // whose status column is null in the database used to take the whole page
  // down here, so coerce rather than trust it.
  const statusClass = (cell: any) => "status " + String(cell ?? "").toLowerCase().replaceAll(" ", "-");

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">{data.eyebrow}</p><h2>{data.title}</h2><p>{data.blurb || "Live records from the Phloz fleet database."}</p></div>{data.action ? <button className="primary module-action" onClick={() => data.actionType ? openAction(data.actionType) : onAction(data.action.replace("+ ", "") + " opened")}>{data.action}</button> : null}</div>
    <div className="module-stats"><div className="module-stat"><span>Total records</span><strong>{loading ? "—" : total}</strong><small>{!loading && total > rows.length ? `Showing the first ${rows.length}` : "Stored in the live database"}</small></div><div className="module-stat"><span>Data source</span><strong>Live</strong><small>EC2 fleet API</small></div><div className="module-stat"><span>Last synchronised</span><strong>Now</strong><small>Refreshes after every save</small></div></div>
    <section className="module-table-card"><div className="module-toolbar"><div><strong>All {name.toLowerCase()}</strong><span>{loading ? "Loading live records…" : visibleRows.length + " live records"}</span></div><div className="toolbar-actions"><input aria-label={"Search " + name} placeholder={"Search " + name.toLowerCase() + "..."} value={query} onChange={e => setQuery(e.target.value)} /><button onClick={load}>↻ Refresh</button><button onClick={() => onAction("Report exported")}>⇩ Export</button></div></div>
      {loadError ? <div className="data-state error">{loadError}</div> : loading ? <div className="data-state">Loading records from EC2…</div> : visibleRows.length === 0 ? <div className="data-state">No records found. Use the action button to create one.</div> :
      <div className="table-wrap"><table><thead><tr>{data.columns.map(col => <th key={col}>{col}</th>)}<th>Action</th></tr></thead><tbody>{visibleRows.map(row => <tr key={row.record.id}>{row.cells.map((cell, j) => <td key={j}>{j === 0 ? <strong>{cell}</strong> : j === row.cells.length - 1 ? <span className={statusClass(cell)}>{cell}</span> : cell}</td>)}<td><button className="row-action" onClick={() => { setSelectedId(row.record.id); setEditing(false); }}>View →</button></td></tr>)}</tbody></table></div>}
    </section>
    {selected && selectedCells && <div className="record-backdrop" onMouseDown={closeDrawer}><aside className="record-drawer" onMouseDown={e => e.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">{data.eyebrow}</p><h2>{selectedCells[0]}</h2><span className={statusClass(selectedCells[selectedCells.length - 1])}>{selectedCells[selectedCells.length - 1]}</span></div><button className="panel-close" onClick={closeDrawer}>×</button></div>
      {editing && editSpec ? <RecordForm spec={editSpec} record={selected} onClose={() => setEditing(false)}
        onSaved={() => { onAction(`${selectedCells[0]} updated`); setEditing(false); load(); }} /> : <>
        <div className="record-fields">{data.columns.map((column, index) => <div className="record-field" key={column}><span>{column}</span><strong>{selectedCells[index] || "—"}</strong></div>)}
          <div className="record-field"><span>Created</span><strong>{stamp(selected.created_at)}</strong></div>
          <div className="record-field"><span>Last updated</span><strong>{stamp(selected.updated_at)}</strong></div>
        </div>
        <div className="record-actions">
          <button className="secondary" onClick={closeDrawer}>Close</button>
          {name === "Invoices" && selected && <button className="secondary" onClick={async () => {
            try {
              const { fmsRequestRaw } = await import("./lib/fms-api");
              const res = await fmsRequestRaw(`invoices/${selected.id}/pdf/`);
              const blob = await res.blob();
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url; a.download = `${selected.number || "invoice"}.pdf`; a.click();
              URL.revokeObjectURL(url);
            } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 120) : "PDF download failed", "warn"); }
          }}>Download PDF</button>}
          {editSpec && <button className="primary" onClick={() => setEditing(true)}>Edit record</button>}
        </div>
      </>}
    </aside></div>}
  </div>;
}

const vehicleStatusOptions: [string, string][] = [
  ["available", "Available"], ["idle", "Idle"], ["allocated", "Allocated"], ["running", "Running"],
  ["loaded", "Loaded"], ["unloaded", "Unloaded"], ["under_maintenance", "Under maintenance"],
  ["driver_unavailable", "Driver unavailable"], ["breakdown", "Breakdown"], ["at_customer_location", "At customer location"],
  ["awaiting_loading", "Awaiting loading"], ["awaiting_unloading", "Awaiting unloading"], ["inactive", "Inactive"],
];

function FleetVehiclesView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [places, setPlaces] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState<any[]>([]);
  const [statusChoice, setStatusChoice] = useState("");
  const [statusReason, setStatusReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [findPlace, setFindPlace] = useState("");
  const [findRadius, setFindRadius] = useState("100");
  const [findBusy, setFindBusy] = useState(false);
  const [availability, setAvailability] = useState<any[] | null>(null);

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("vehicles/")).then(payload => {
      const records = asList(payload);
      setVehicles(records);
      setTotal(asCount(payload, records));
    }).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);
  useEffect(() => { fmsRequest<any>(wholeSet("places/")).then(payload => setPlaces(asList(payload))).catch(() => undefined); }, []);

  useEffect(() => {
    if (!selected) { setHistory([]); return; }
    setStatusChoice(""); setStatusReason("");
    fmsRequest<any>(`vehicles/${selected.id}/status-history/`).then(setHistory).catch(() => setHistory([]));
  }, [selected?.id]);

  const rows = vehicles.filter(v => `${v.registration_number} ${v.vehicle_type} ${v.ownership} ${v.vendor_name || ""} ${v.status}`.toLowerCase().includes(query.toLowerCase()));

  const changeStatus = async () => {
    if (!selected || !statusChoice) return;
    setBusy(true);
    try {
      const updated = await fmsRequest<any>(`vehicles/${selected.id}/set-status/`, { method: "POST", body: JSON.stringify({ status: statusChoice, reason: statusReason }) });
      onAction(`${updated.registration_number} moved to ${statusChoice.replaceAll("_", " ")}`);
      setSelected(updated);
      setStatusChoice(""); setStatusReason("");
      fmsRequest<any>(`vehicles/${selected.id}/status-history/`).then(setHistory).catch(() => undefined);
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not change status", "warn");
    } finally { setBusy(false); }
  };

  const findAvailability = async () => {
    setFindBusy(true);
    try {
      const params = new URLSearchParams();
      if (findPlace) {
        params.set("place", findPlace);
        // A radius only means something relative to a place - applying it with no
        // place picked would filter out every vehicle, since none would have a
        // computed distance to compare against it.
        if (findRadius) params.set("radius_km", findRadius);
      }
      const payload = await fmsRequest<any>(`vehicles/availability/?${params.toString()}`);
      setAvailability(payload.vehicles || []);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not search availability", "warn");
    } finally { setFindBusy(false); }
  };

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">OWN FLEET</p><h2>Vehicles & availability</h2><p>Own, attached and outside-sourced vehicles, live status and where a truck can be found near a pickup point.</p></div><button className="primary module-action" onClick={() => openAction("vehicle")}>＋ Add vehicle</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Fleet size</span><strong>{loading ? "—" : total}</strong><small>Own, attached & outside-sourced</small></div>
      <div className="module-stat"><span>Available now</span><strong>{loading ? "—" : vehicles.filter(v => ["available", "idle"].includes(v.status)).length}</strong><small>Ready for allocation</small></div>
      <div className="module-stat"><span>Attention needed</span><strong>{loading ? "—" : vehicles.filter(v => ["breakdown", "under_maintenance", "driver_unavailable"].includes(v.status)).length}</strong><small>Breakdown, workshop or no driver</small></div>
    </div>

    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>Find available vehicles</strong><span>Nearest first, with a document-expiry flag</span></div></div>
      <div className="allocate-grid" style={{ gridTemplateColumns: "1fr 140px auto" }}>
        <label>Near place<select value={findPlace} onChange={e => setFindPlace(e.target.value)}><option value="">Any location</option>{places.map(p => <option key={p.id} value={p.id}>{p.name} · {p.city}</option>)}</select></label>
        <label>Radius (km)<input value={findRadius} onChange={e => setFindRadius(e.target.value)} type="number" min="1" /></label>
        <button className="secondary full-button" disabled={findBusy} onClick={findAvailability}>{findBusy ? "Searching…" : "Find"}</button>
      </div>
      {availability && (availability.length ? <div className="table-wrap"><table><thead><tr><th>Vehicle</th><th>Status</th><th>Distance</th><th>Current place</th><th>Documents</th></tr></thead>
        <tbody>{availability.map(row => <tr key={row.id} className="clickable" onClick={() => setSelected(vehicles.find(v => v.id === row.id) || row)}>
          <td><strong>{row.registration_number}</strong><small>{row.vehicle_type}</small></td>
          <td><span className={"status " + String(row.status).replaceAll("_", "-")}>{String(row.status).replaceAll("_", " ")}</span></td>
          <td>{row.distance_km == null ? "—" : `${Number(row.distance_km).toFixed(1)} km`}</td>
          <td>{row.current_place || "—"}</td>
          <td>{row.expired_documents ? <span className="status expired">{row.expired_documents} expired</span> : "Valid"}</td>
        </tr>)}</tbody></table></div>
        : <div className="data-state">No vehicle matched that location and radius.</div>)}
    </section>

    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>All vehicles</strong><span>{loading ? "Loading…" : rows.length + " vehicles"}</span></div><div className="toolbar-actions"><input aria-label="Search vehicles" placeholder="Search vehicles..." value={query} onChange={e => setQuery(e.target.value)} /><button onClick={load}>↻ Refresh</button></div></div>
      {loading ? <div className="data-state">Loading records…</div> : !rows.length ? <div className="data-state">No vehicles found.</div> :
      <div className="table-wrap"><table><thead><tr><th>Vehicle</th><th>Type</th><th>Ownership</th><th>Capacity</th><th>Status</th></tr></thead>
        <tbody>{rows.map(v => <tr key={v.id} className="clickable" onClick={() => setSelected(v)}>
          <td><strong>{v.registration_number}</strong></td>
          <td>{v.vehicle_type}</td>
          <td>{v.ownership === "own" ? "Own" : `${v.ownership.replace("_", " ")}${v.vendor_name ? ` · ${v.vendor_name}` : ""}`}</td>
          <td>{Number(v.capacity_kg).toLocaleString("en-IN")} kg</td>
          <td><span className={"status " + String(v.status).replaceAll("_", "-")}>{String(v.status).replaceAll("_", " ")}</span></td>
        </tr>)}</tbody></table></div>}
    </section>

    {selected && <div className="record-backdrop" onMouseDown={() => setSelected(null)}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">VEHICLE</p><h2>{selected.registration_number}</h2><span className={"status " + String(selected.status).replaceAll("_", "-")}>{String(selected.status).replaceAll("_", " ")}</span></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>
      {editing ? <RecordForm spec={recordForms.vehicle} record={selected} onClose={() => setEditing(false)}
        onSaved={() => { onAction(`${selected.registration_number} updated`); setEditing(false); load(); }} /> : <>
        <div className="record-fields">
          <div className="record-field"><span>Type</span><strong>{selected.vehicle_type}</strong></div>
          <div className="record-field"><span>Ownership</span><strong>{selected.ownership}{selected.vendor_name ? ` · ${selected.vendor_name}` : ""}</strong></div>
          <div className="record-field"><span>Capacity</span><strong>{Number(selected.capacity_kg).toLocaleString("en-IN")} kg</strong></div>
          <div className="record-field"><span>Odometer</span><strong>{Number(selected.current_odometer_km).toLocaleString("en-IN")} km</strong></div>
          <div className="record-field"><span>Current place</span><strong>{selected.current_place_name || "—"}</strong></div>
          <div className="record-field"><span>Current trip</span><strong>{selected.current_trip_number || "—"}</strong></div>
          <div className="record-field"><span>Status since</span><strong>{stamp(selected.status_since)}</strong></div>
          <div className="record-field"><span>Expected available</span><strong>{selected.expected_available_at ? stamp(selected.expected_available_at) : "—"}</strong></div>
          <div className="record-field"><span>Insurance valid until</span><strong>{selected.insurance_expiry || "—"}</strong></div>
          <div className="record-field"><span>Permit valid until</span><strong>{selected.permit_expiry || "—"}</strong></div>
        </div>

        <div className="allocate-box">
          <p className="eyebrow">CHANGE STATUS</p>
          <div className="allocate-grid">
            <label>New status<select value={statusChoice} onChange={e => setStatusChoice(e.target.value)}><option value="">Select a status</option>{vehicleStatusOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          </div>
          <label className="reject-reason">Reason<input value={statusReason} onChange={e => setStatusReason(e.target.value)} placeholder="Clutch failure near Lonavala" /></label>
          <button className="primary full-button" disabled={busy || !statusChoice} onClick={changeStatus}>Update status</button>
        </div>

        <div className="record-timeline"><p className="eyebrow">STATUS HISTORY</p>
          {history.slice(0, 12).map((row: any) => <div key={row.id}><i /><span><strong>{String(row.status).replaceAll("_", " ")}</strong><small>{row.reason || row.place_name || "—"}</small></span><time>{stamp(row.started_at)}</time></div>)}
          {!history.length && <div><i /><span><strong>No history yet</strong></span></div>}
        </div>

        <div className="record-actions"><button className="secondary" onClick={() => setSelected(null)}>Close</button><button className="primary" onClick={() => setEditing(true)}>Edit record</button></div>
      </>}
    </aside></div>}
  </div>;
}

function FleetsView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
  const [fleets, setFleets] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [drivers, setDrivers] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [addVehicle, setAddVehicle] = useState("");
  const [addDriver, setAddDriver] = useState("");

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("fleets/")).then(payload => setFleets(asList(payload))).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);
  useEffect(() => {
    fmsRequest<any>(wholeSet("vehicles/")).then(payload => setVehicles(asList(payload))).catch(() => undefined);
    fmsRequest<any>(wholeSet("drivers/")).then(payload => setDrivers(asList(payload))).catch(() => undefined);
  }, []);

  const selected = fleets.find(fleet => fleet.id === selectedId) || null;
  const vehicleLabel = (id: number) => { const v = vehicles.find(record => record.id === id); return v ? `${v.registration_number} · ${v.vehicle_type}` : `#${id}`; };
  const driverLabel = (id: number) => { const d = drivers.find(record => record.id === id); return d ? d.name : `#${id}`; };
  const unassignedVehicles = vehicles.filter(v => !(selected?.vehicles || []).includes(v.id));
  const unassignedDrivers = drivers.filter(d => !(selected?.drivers || []).includes(d.id));

  const mutate = async (body: Record<string, unknown>, message: string) => {
    setBusy(true);
    try {
      await fmsRequest(`fleets/${selectedId}/assign/`, { method: "POST", body: JSON.stringify(body) });
      onAction(message);
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Action failed", "warn");
    } finally { setBusy(false); }
  };

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">FLEET GROUPS</p><h2>Fleets</h2><p>Group owned and attached vehicles with their drivers. Click a fleet to assign vehicles and drivers.</p></div><button className="primary module-action" onClick={() => openAction("fleet")}>＋ Create fleet</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>Fleets</span><strong>{loading ? "—" : fleets.length}</strong><small>Owned and attached groups</small></div>
      <div className="module-stat"><span>Vehicles</span><strong>{vehicles.length}</strong><small>Across the whole fleet</small></div>
      <div className="module-stat"><span>Drivers</span><strong>{drivers.length}</strong><small>Across the whole fleet</small></div>
    </div>
    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>All fleets</strong><span>{fleets.length} groups</span></div></div>
      <div className="table-wrap"><table><thead><tr><th>Fleet</th><th>Service area</th><th>Vendor</th><th>Vehicles</th><th>Drivers</th><th>Status</th></tr></thead>
        <tbody>{fleets.map(fleet => <tr key={fleet.id} className="clickable" onClick={() => { setSelectedId(fleet.id); setAddVehicle(""); setAddDriver(""); }}>
          <td><strong>{fleet.name}</strong><small>{fleet.code}</small></td>
          <td>{fleet.service_area_name || "—"}</td>
          <td>{fleet.vendor_name || "Owned"}</td>
          <td>{fleet.vehicle_count}</td>
          <td>{fleet.driver_count}</td>
          <td><span className={"status " + fleet.status}>{fleet.status}</span></td>
        </tr>)}</tbody></table></div>
      {!loading && !fleets.length && <div className="data-state">No fleets yet. Create one to start grouping vehicles and drivers.</div>}
    </section>

    {selected && <div className="record-backdrop" onMouseDown={() => setSelectedId(null)}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">FLEET {selected.code}</p><h2>{selected.name}</h2><span className={"status " + selected.status}>{selected.status}</span></div><button className="panel-close" onClick={() => setSelectedId(null)}>×</button></div>
      <div className="record-fields">
        <div className="record-field"><span>Service area</span><strong>{selected.service_area_name || "—"}</strong></div>
        <div className="record-field"><span>Vendor</span><strong>{selected.vendor_name || "Owned fleet"}</strong></div>
        <div className="record-field"><span>Manager</span><strong>{selected.manager || "—"}</strong></div>
        <div className="record-field"><span>Vehicles · drivers</span><strong>{selected.vehicle_count} · {selected.driver_count}</strong></div>
        <div className="record-field"><span>Created</span><strong>{stamp(selected.created_at)}</strong></div>
        <div className="record-field"><span>Last updated</span><strong>{stamp(selected.updated_at)}</strong></div>
      </div>

      <div className="allocate-box">
        <p className="eyebrow">VEHICLES</p>
        <div className="chip-list">
          {(selected.vehicles || []).map((id: number) => <span className="chip removable" key={id}>{vehicleLabel(id)}<button disabled={busy} onClick={() => mutate({ vehicles: [id], remove: true }, "Vehicle removed from fleet")}>×</button></span>)}
          {!(selected.vehicles || []).length && <p className="pod-note">No vehicles assigned yet.</p>}
        </div>
        <div className="assign-row">
          <select value={addVehicle} onChange={event => setAddVehicle(event.target.value)}>
            <option value="">Add a vehicle…</option>
            {unassignedVehicles.map(v => <option key={v.id} value={v.id}>{v.registration_number} · {v.vehicle_type}</option>)}
          </select>
          <button className="secondary" disabled={busy || !addVehicle} onClick={() => { mutate({ vehicles: [Number(addVehicle)] }, "Vehicle assigned to fleet"); setAddVehicle(""); }}>Add</button>
        </div>
      </div>

      <div className="allocate-box">
        <p className="eyebrow">DRIVERS</p>
        <div className="chip-list">
          {(selected.drivers || []).map((id: number) => <span className="chip removable" key={id}>{driverLabel(id)}<button disabled={busy} onClick={() => mutate({ drivers: [id], remove: true }, "Driver removed from fleet")}>×</button></span>)}
          {!(selected.drivers || []).length && <p className="pod-note">No drivers assigned yet.</p>}
        </div>
        <div className="assign-row">
          <select value={addDriver} onChange={event => setAddDriver(event.target.value)}>
            <option value="">Add a driver…</option>
            {unassignedDrivers.map(d => <option key={d.id} value={d.id}>{d.name} · {d.phone}</option>)}
          </select>
          <button className="secondary" disabled={busy || !addDriver} onClick={() => { mutate({ drivers: [Number(addDriver)] }, "Driver assigned to fleet"); setAddDriver(""); }}>Add</button>
        </div>
      </div>
    </aside></div>}
  </div>;
}

const fleetOpsPages = ["Dispatch", "Drivers", "Maintenance", "Analytics"];

// Mirrors backend fleet.models.EXPENSE_CATEGORIES - the cost lines a transport
// office's own trip-settlement register breaks a trip's expense down into.
const tripExpenseCategories: [string, string][] = [
  ["diesel", "Diesel / running"], ["toll", "Toll (FASTag)"], ["cash_toll", "Cash toll"],
  ["driver_allowance", "Driver bhatta"], ["loading", "Loading"], ["unloading", "Unloading"],
  ["halting", "Halting / waiting"], ["police", "Police / checkpost"], ["permit", "Permit / state tax"],
  ["way_side", "Way side expense"], ["kata", "Weighbridge / kata"], ["parking", "Parking"],
  ["repair", "Repair / R&M"], ["adblue", "AdBlue"], ["salary", "Driver salary"],
  ["rto_fine", "RTO fine"], ["other", "Other"],
];

// The trip sheet a transport office already fills in by hand: load type, load/unload
// dates, planned vs. billable distance, odometer, freight, the diesel/cash advance,
// and every expense line - captured here in one submission and settled automatically
// (total expense, diesel difference, cost/revenue per km, trip profit).
function TripSettlementPanel({ trip, onAction, onSaved }: { trip: any; onAction: Notify; onSaved: (trip: any) => void }) {
  const [loading, setLoading] = useState(true);
  const [summary, setSummary] = useState<any>(null);
  const [expenses, setExpenses] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLoading(true);
    fmsRequest<any>(`trips/${trip.id}/settlement/`).then(payload => {
      setSummary(payload.summary);
      setExpenses(payload.expenses || {});
    }).catch(() => undefined).finally(() => setLoading(false));
  }, [trip.id]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const num = (name: string) => { const raw = form.get(name); return raw ? Number(raw) : undefined; };
    const str = (name: string) => { const raw = String(form.get(name) || ""); return raw || undefined; };
    const expensePayload: Record<string, number> = {};
    tripExpenseCategories.forEach(([code]) => {
      const raw = form.get(`exp_${code}`);
      if (raw !== null && String(raw) !== "") expensePayload[code] = Number(raw);
    });
    setBusy(true);
    try {
      const payload = await fmsRequest<any>(`trips/${trip.id}/settlement/`, { method: "POST", body: JSON.stringify({
        load_type: str("load_type"), load_date: str("load_date"), unload_date: str("unload_date"),
        google_km: num("google_km"), passed_km: num("passed_km"),
        start_odometer_km: num("start_odometer_km"), end_odometer_km: num("end_odometer_km"),
        freight_amount: num("freight_amount"), diesel_given: num("diesel_given"), expenses: expensePayload,
      }) });
      onAction("Trip settlement saved");
      setSummary(payload.summary);
      setExpenses(payload.expenses || {});
      onSaved(payload.trip);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 150) : "Could not save the trip settlement", "warn");
    } finally { setBusy(false); }
  };

  if (loading) return <div className="record-section"><p className="eyebrow">TRIP SETTLEMENT</p><div className="data-state">Loading…</div></div>;

  return <form className="record-section" onSubmit={submit}>
    <p className="eyebrow">TRIP SETTLEMENT</p>
    <div className="form-grid">
      <label>Load type<select name="load_type" defaultValue={trip.load_type || ""}>
        <option value="">Select…</option>
        <option value="dry">Dry</option><option value="chiller">Chiller</option>
        <option value="frozen">Frozen</option><option value="empty">Empty (no cargo)</option>
      </select></label>
      <label>Freight (₹)<input name="freight_amount" type="number" step="any" defaultValue={trip.freight_amount || ""} placeholder="From linked order if blank" /></label>
      <label>Load date<input name="load_date" type="date" defaultValue={trip.load_date || ""} /></label>
      <label>Unload date<input name="unload_date" type="date" defaultValue={trip.unload_date || ""} /></label>
      <label>Google km (planned)<input name="google_km" type="number" defaultValue={trip.google_km || ""} /></label>
      <label>Passed km (billable)<input name="passed_km" type="number" defaultValue={trip.passed_km || ""} /></label>
      <label>Start odometer<input name="start_odometer_km" type="number" defaultValue={trip.start_odometer_km ?? ""} /></label>
      <label>End odometer<input name="end_odometer_km" type="number" defaultValue={trip.end_odometer_km ?? ""} /></label>
      <label>Diesel given / advance (₹)<input name="diesel_given" type="number" step="any" defaultValue={trip.advance_amount || ""} /></label>
    </div>
    <p className="eyebrow" style={{ marginTop: 16 }}>EXPENSES (₹)</p>
    <div className="form-grid">
      {tripExpenseCategories.map(([code, label]) => <label key={code}>{label}
        <input name={`exp_${code}`} type="number" step="any" defaultValue={expenses[code] ?? ""} />
      </label>)}
    </div>
    {summary && <div className="margin-strip" style={{ marginTop: 16 }}>
      <div><span>Total expense</span><strong>{rupees(summary.total_exp)}</strong></div>
      <div><span>Diesel given</span><strong>{rupees(summary.diesel_given)}</strong></div>
      <div><span>Difference</span><strong className={summary.difference <= 0 ? "good" : "bad"}>{rupees(summary.difference)}</strong></div>
      <div><span>Per km cost</span><strong>₹{summary.per_km_exp}</strong></div>
      <div><span>Per km revenue</span><strong>₹{summary.per_km_rev}</strong></div>
      <div><span>Trip profit</span><strong className={summary.trip_profit >= 0 ? "good" : "bad"}>{rupees(summary.trip_profit)}</strong></div>
    </div>}
    <button className="primary full-button" disabled={busy} style={{ marginTop: 16 }}>{busy ? "Saving…" : "Save trip settlement"}</button>
  </form>;
}

function FleetOpsView({ name, onAction, reloadKey, openAction }: { name: string; onAction: Notify; reloadKey: number; openAction: (type: string) => void }) {
  const [records, setRecords] = useState<any[]>([]);
  const [dashboard, setDashboard] = useState<any>(null);
  const [tripDetail, setTripDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  // Milk-run: orders available to link to the open trip
  const [linkOrders, setLinkOrders] = useState<any[]>([]);
  const [linkOrderId, setLinkOrderId] = useState("");
  const [linking, setLinking] = useState(false);
  const endpoint = name === "Dispatch" ? "trips/" : name === "Drivers" ? "drivers/" : name === "Maintenance" ? "maintenance/" : "analytics/fleet/";
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
  // When a trip is opened, fetch orders not on any trip yet so the operator can
  // add them as milk-run legs. Completed and cancelled orders are excluded.
  const openTripDetail = async (trip: any) => {
    setTripDetail(trip); setLinkOrderId("");
    try {
      const payload = await fmsRequest<any>(wholeSet("orders/") + "&status=dispatched,in_transit,assigned,created");
      const all: any[] = asList(payload);
      setLinkOrders(all.filter(o => !o.trip || o.trip === trip.id));
    } catch { setLinkOrders([]); }
  };
  const refreshTripDetail = async (trip: any) => {
    try {
      const updated = await fmsRequest<any>(`trips/${trip.id}/`);
      setTripDetail(updated);
      setRecords(prev => prev.map(r => r.id === updated.id ? updated : r));
    } catch { /* ignore */ }
  };
  const addOrderToTrip = async () => {
    if (!tripDetail || !linkOrderId) return;
    setLinking(true);
    try {
      const updated = await fmsRequest<any>(`trips/${tripDetail.id}/add-order/`, { method: "POST", body: JSON.stringify({ order: Number(linkOrderId) }) });
      onAction("Order linked to trip");
      setTripDetail(updated); setLinkOrderId("");
      const linked = updated.linked_orders?.map((o: any) => o.id) || [];
      setLinkOrders(prev => prev.filter(o => !linked.includes(o.id)));
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not link order", "warn"); }
    finally { setLinking(false); }
  };
  const removeOrderFromTrip = async (orderId: number) => {
    if (!tripDetail) return;
    try {
      const updated = await fmsRequest<any>(`trips/${tripDetail.id}/remove-order/`, { method: "POST", body: JSON.stringify({ order: orderId }) });
      onAction("Order unlinked from trip");
      setTripDetail(updated);
    } catch (e) { onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not unlink order", "warn"); }
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
      {records.filter(r => r.status === status).map(trip => <article key={trip.id} {...board.cardProps(trip, openTripDetail)}>
        <b>{trip.number}</b><p>{trip.origin} → {trip.destination}</p>
        <small>{trip.vehicle_number} · {trip.driver_name}</small>
        {(trip.linked_orders || []).length > 0 && <small style={{ color: "var(--brand)" }}>{(trip.linked_orders || []).length} order{(trip.linked_orders || []).length > 1 ? "s" : ""} linked</small>}
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
               ["Load type", tripDetail.load_type || "—"], ["Running km", tripDetail.running_km ?? "—"],
               ["Planned departure", tripDetail.planned_departure ? new Date(tripDetail.planned_departure).toLocaleString("en-IN") : ""],
               ["Actual departure", tripDetail.actual_departure ? new Date(tripDetail.actual_departure).toLocaleString("en-IN") : ""],
               ["Arrived", tripDetail.arrival_at ? new Date(tripDetail.arrival_at).toLocaleString("en-IN") : ""],
               ["Diesel given", rupees(tripDetail.advance_amount)], ["Estimated cost", rupees(tripDetail.estimated_cost)],
               ["Created", stamp(tripDetail.created_at)], ["Last updated", stamp(tripDetail.updated_at)]]}
      sections={[{ label: "GPS EVENTS", rows: (tripDetail.tracking_events || []).slice(0, 8).map((event: any) => ({
        key: String(event.id), primary: event.description || event.event_type,
        secondary: `${event.speed_kph} km/h`, meta: new Date(event.recorded_at).toLocaleString("en-IN") })) }]}>
      {/* --- Milk-run: linked orders --- */}
      <div className="allocate-box">
        <p className="eyebrow">LINKED ORDERS (MILK RUN)</p>
        {(tripDetail.linked_orders || []).length === 0
          ? <p className="pod-note">No orders linked yet. Add below to run multiple consignments on this trip.</p>
          : <div className="shipment-rows" style={{ maxHeight: 180, overflowY: "auto", marginBottom: 8 }}>
            {(tripDetail.linked_orders || []).map((o: any) => <div key={o.id} className="shipment-row">
              <div style={{ minWidth: 0 }}>
                <strong>{o.number}</strong>
                <small>{o.customer_name} · {o.route}</small>
              </div>
              <div className="shipment-row-meta">
                <span className={"status " + o.status}>{o.status}</span>
                <button type="button" className="secondary" style={{ marginLeft: 8, padding: "2px 8px", fontSize: 12 }}
                  onClick={() => removeOrderFromTrip(o.id)}>Remove</button>
              </div>
            </div>)}
          </div>}
        {linkOrders.length > 0 && <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select value={linkOrderId} onChange={event => setLinkOrderId(event.target.value)}
                  style={{ flex: 1 }} aria-label="Order to link">
            <option value="">Select an order to add…</option>
            {linkOrders.filter(o => !(tripDetail.linked_orders || []).some((lo: any) => lo.id === o.id))
              .map((o: any) => <option key={o.id} value={o.id}>{o.number} · {o.customer_name} ({o.status})</option>)}
          </select>
          <button className="primary" disabled={!linkOrderId || linking} onClick={addOrderToTrip}
                  style={{ whiteSpace: "nowrap" }}>{linking ? "Linking…" : "Add to trip"}</button>
        </div>}
      </div>
      <TripSettlementPanel trip={tripDetail} onAction={onAction} onSaved={updated => { setTripDetail(updated); load(); }} />
      <div className="record-actions" style={{ marginTop: 18 }}>
        <button className="secondary" onClick={() => setTripDetail(null)}>Close</button>
        {tripDetail.status === "planned" && <button className="primary" onClick={() => tripAction(tripDetail, "dispatch")}>Dispatch trip</button>}
        {["dispatched", "in_transit"].includes(tripDetail.status) && <button className="primary" onClick={() => tripAction(tripDetail, "close")}>Close trip</button>}
      </div>
    </DetailDrawer>}
  </div>;
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
const stamp = (value: any) => (value ? new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—");
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
function DetailDrawer({ eyebrow, title, status, fields, sections, children, actions, onClose }: {
  eyebrow: string; title: string; status?: string;
  fields: [string, any][];
  sections?: { label: string; rows: { key: string; primary: string; secondary?: string; meta?: string }[] }[];
  children?: React.ReactNode;
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
    {children}
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
  const [candidates, setCandidates] = useState<any[] | null>(null);
  const [recommending, setRecommending] = useState(false);
  const [spotCandidate, setSpotCandidate] = useState<any>(null);
  const [settlement, setSettlement] = useState<any>(null);

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

  // The candidate list and any spot-hire form in progress belong to whichever order
  // is open, so a fresh selection starts clean rather than showing the last order's picks.
  useEffect(() => { setCandidates(null); setSpotCandidate(null); }, [selected?.id]);

  // The settlement sheet is safe to fetch for any order - it comes back with nulls
  // for the sides that have not happened yet rather than an error.
  useEffect(() => {
    if (!selected) { setSettlement(null); return; }
    let active = true;
    fmsRequest<any>(`orders/${selected.id}/settlement/`).then(payload => { if (active) setSettlement(payload); }).catch(() => { if (active) setSettlement(null); });
    return () => { active = false; };
  }, [selected?.id, selected?.status]);

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

  // Own and vendor capacity ranked by expected profit, not distance - the point of
  // Phase 4 is that a dispatcher confirms a decision here rather than guesses one.
  const recommend = async () => {
    if (!selected) return;
    setRecommending(true); setSpotCandidate(null);
    try {
      const payload = await fmsRequest<any>(`orders/${selected.id}/recommend-vehicles/`, { method: "POST", body: "{}" });
      setCandidates(payload.candidates || []);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not fetch recommendations", "warn");
    } finally { setRecommending(false); }
  };

  // A candidate that already has a vehicle on file (own, or a vendor's registered
  // truck) confirms in one call; the driver picked above goes along if one was chosen.
  const confirmVehicle = async (candidate: any) => {
    setBusy(true);
    try {
      const payload = await fmsRequest<any>(`orders/${selected.id}/confirm-vehicle/`, { method: "POST", body: JSON.stringify({
        vehicle: candidate.vehicle_id, ...(driver ? { driver: Number(driver) } : {}),
      }) });
      onAction(`${payload.order.number} confirmed on ${candidate.registration_number}`);
      (payload.warnings || []).forEach((warning: string) => onAction(warning, "warn"));
      setCandidates(null);
      setSelected(payload.order);
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not confirm the vehicle", "warn");
    } finally { setBusy(false); }
  };

  // A spot-hire candidate has no vehicle on file yet, so the driver's own truck and
  // driver details are captured here and the hire is created in the same call.
  const confirmSpotHire = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!spotCandidate) return;
    const form = new FormData(event.currentTarget as HTMLFormElement);
    setBusy(true);
    try {
      const payload = await fmsRequest<any>(`orders/${selected.id}/confirm-vehicle/`, { method: "POST", body: JSON.stringify({
        vendor: spotCandidate.vendor_id,
        outside_vehicle: { vehicle_number: String(form.get("vehicle_number") || ""), vehicle_type: String(form.get("vehicle_type") || "") },
        hire: { agreed_rate: Number(form.get("agreed_rate") || 0), rate_basis: "trip",
               driver_name: String(form.get("driver_name") || ""), driver_phone: String(form.get("driver_phone") || ""),
               advance_amount: Number(form.get("advance_amount") || 0) },
      }) });
      onAction(`${payload.order.number} confirmed via ${spotCandidate.vendor_name}${payload.vendor_confirmation_sent ? " · vendor emailed" : ""}`);
      (payload.warnings || []).forEach((warning: string) => onAction(warning, "warn"));
      setCandidates(null); setSpotCandidate(null);
      setSelected(payload.order);
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Could not confirm the hire", "warn");
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
        <div className="record-field"><span>Created</span><strong>{stamp(selected.created_at)}</strong></div>
        <div className="record-field"><span>Last updated</span><strong>{stamp(selected.updated_at)}</strong></div>
      </div>
      {selected.status === "created" || !selected.driver ? <div className="allocate-box">
        <p className="eyebrow">ALLOCATE VEHICLE & DRIVER</p>
        <div className="allocate-grid">
          <label>Driver (optional for a vendor hire)<select value={driver} onChange={event => setDriver(event.target.value)}><option value="">Select driver</option>{drivers.map(record => <option key={record.id} value={record.id}>{record.name} · {record.status}</option>)}</select></label>
        </div>
        <button className="secondary full-button" disabled={recommending} onClick={recommend}>{recommending ? "Finding the best truck…" : candidates ? "↻ Re-run recommendation" : "Find best vehicle"}</button>

        {candidates && (candidates.length ? <div className="candidate-list">{candidates.map(candidate => <div
              className={"allocate-box candidate-card" + (candidate.recommended ? " best" : "")}
              key={candidate.vehicle_id ?? `spot-${candidate.vendor_id}`}>
            <div className="candidate-card-head">
              <span className="rank-pill">{candidate.rank}</span>
              <div>
                <strong>{candidate.registration_number || "Spot hire"}</strong>
                <small>{candidate.vendor_name || "Own fleet"}{candidate.vehicle_type ? ` · ${candidate.vehicle_type}` : ""} · {String(candidate.source).replaceAll("_", " ")}{candidate.dead_km != null ? ` · ${Number(candidate.dead_km).toFixed(1)} dead km` : ""}</small>
                {candidate.fit_notes?.length ? <small className="text-late">{candidate.fit_notes.join(" ")}</small> : null}
              </div>
              {candidate.vehicle_id
                ? <button className="secondary" disabled={busy} onClick={() => confirmVehicle(candidate)}>Confirm</button>
                : <button className="secondary" disabled={busy} onClick={() => setSpotCandidate((current: any) => current?.vendor_id === candidate.vendor_id ? null : candidate)}>{spotCandidate?.vendor_id === candidate.vendor_id ? "Cancel" : "Confirm"}</button>}
            </div>
            <div className="margin-strip">
              <div><span>Cost</span><strong>{rupees(candidate.expected_cost)}</strong>{candidate.estimated_cost ? <span className="estimate-note">estimated</span> : null}</div>
              <div><span>Revenue</span><strong>{rupees(candidate.expected_revenue)}</strong></div>
              <div><span>Profit</span><strong className={candidate.expected_profit >= 0 ? "good" : "bad"}>{rupees(candidate.expected_profit)}</strong></div>
            </div>
          </div>)}</div>
          : <p className="pod-note">No vehicle or active vendor matched this consignment yet.</p>)}

        {spotCandidate && <form className="action-form pod-capture" onSubmit={confirmSpotHire}>
          <p className="eyebrow">CONFIRM SPOT HIRE · {spotCandidate.vendor_name}</p>
          <div className="form-grid">
            <label>Vehicle number<input name="vehicle_number" required /></label>
            <label>Vehicle type<input name="vehicle_type" placeholder="20 ft SXL" /></label>
            <label>Driver name<input name="driver_name" required /></label>
            <label>Driver phone<input name="driver_phone" /></label>
            <label>Agreed rate (₹)<input name="agreed_rate" type="number" step="any" defaultValue={spotCandidate.expected_cost} required /></label>
            <label>Advance paid (₹)<input name="advance_amount" type="number" step="any" defaultValue="0" /></label>
          </div>
          <button className="primary full-button" disabled={busy}>Confirm hire & notify vendor</button>
        </form>}

        <p className="eyebrow" style={{ marginTop: 18 }}>OR ASSIGN DIRECTLY</p>
        <div className="allocate-grid">
          <label>Vehicle<select value={vehicle} onChange={event => setVehicle(event.target.value)}><option value="">Select vehicle</option>{vehicles.map(record => <option key={record.id} value={record.id}>{record.registration_number} · {record.status}</option>)}</select></label>
        </div>
        <button className="primary full-button" disabled={busy || !driver || !vehicle} onClick={() => run(selected, "assign", { driver: Number(driver), vehicle: Number(vehicle) })}>Assign to order</button>
      </div> : <div className="allocate-box"><p className="eyebrow">ALLOCATION</p><div className="tracking-grid">
          <div><span>Vehicle</span><strong>{selected.vehicle_number || "—"}</strong></div>
          <div><span>Driver</span><strong>{selected.driver_name || "—"}</strong></div>
          <div><span>Source</span><strong>{settlement?.vendor ? `Vendor · ${settlement.vendor.vendor}` : "Own fleet"}</strong></div>
          <div><span>Hire status</span><strong>{settlement?.vendor ? settlement.vendor.hire_status : "—"}</strong></div>
        </div></div>}

      {settlement && (settlement.customer || settlement.vendor || settlement.vehicle.fuel || settlement.vehicle.trip_expenses) && <div className="record-section">
        <p className="eyebrow">SETTLEMENT</p>
        <div className="margin-strip">
          <div><span>Revenue</span><strong>{rupees(settlement.revenue)}</strong></div>
          <div><span>Total cost</span><strong>{rupees(settlement.total_cost)}</strong></div>
          <div><span>Actual profit</span><strong className={settlement.actual_profit >= 0 ? "good" : "bad"}>{rupees(settlement.actual_profit)}</strong></div>
        </div>
        <div className="tracking-grid">
          <div><span>Customer billed</span><strong>{settlement.customer ? rupees(settlement.customer.total_amount) : "Not invoiced"}</strong></div>
          <div><span>Customer outstanding</span><strong>{settlement.customer ? rupees(settlement.customer.outstanding) : "—"}</strong></div>
          <div><span>Vendor payable</span><strong>{settlement.vendor ? rupees(settlement.vendor.payable.total_payable) : "Own vehicle"}</strong></div>
          <div><span>Vendor balance due</span><strong>{settlement.vendor ? rupees(settlement.vendor.payable.balance_due) : "—"}</strong></div>
          <div><span>Fuel</span><strong>{rupees(settlement.vehicle.fuel)}</strong></div>
          <div><span>On-road expenses</span><strong>{rupees(settlement.vehicle.trip_expenses)}</strong></div>
        </div>
      </div>}
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

const hireStatusLabel: Record<string, string> = {
  draft: "Draft", confirmed: "Confirmed", billed: "Billed", settled: "Settled", cancelled: "Cancelled",
};

// CVRP dispatch planning - see docs/DISPATCH-PLANNING.md. Deliberately a simpler
// list-and-drawer UI rather than the Gantt/map board the design describes: this
// is the "costed plan on screen" milestone, not the full console build-out.
const planStatusLabel: Record<string, string> = {
  draft: "Draft", ready: "Ready to solve", solving: "Solving", solved: "Solved",
  failed: "Failed", committed: "Committed", superseded: "Superseded",
};

function DispatchPlanningView({ onAction }: { onAction: Notify }) {
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [drivers, setDrivers] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [newDate, setNewDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [strategies, setStrategies] = useState<any[]>([]);
  const [strategyName, setStrategyName] = useState("balanced");
  const [weightOverrides, setWeightOverrides] = useState<Record<string, number>>({});
  const [constraintOverrides, setConstraintOverrides] = useState<Record<string, any>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [customersList, setCustomersList] = useState<any[]>([]);
  const [placesList, setPlacesList] = useState<any[]>([]);
  const [showCollectFilters, setShowCollectFilters] = useState(false);
  const [collectFilters, setCollectFilters] = useState<{ temperature_class: string; scheduled_from: string; scheduled_to: string; customers: string[]; pickup_places: string[]; include_indents: boolean }>({
    temperature_class: "", scheduled_from: "", scheduled_to: "", customers: [], pickup_places: [], include_indents: true,
  });
  const [routeSort, setRouteSort] = useState<{ key: string; dir: 1 | -1 }>({ key: "sequence", dir: 1 });
  const [expandedRoute, setExpandedRoute] = useState<number | null>(null);
  const [mapData, setMapData] = useState<{ routes: any[]; unrouted_tasks: any[] } | null>(null);

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("dispatch/plans/")).then(payload => setPlans(asList(payload))).finally(() => setLoading(false));
  };
  useEffect(load, []);
  useEffect(() => { fmsRequest<any>(wholeSet("drivers/")).then(payload => setDrivers(asList(payload))).catch(() => undefined); }, []);
  useEffect(() => { fmsRequest<any>("dispatch/strategies/").then(payload => setStrategies(asList(payload))).catch(() => undefined); }, []);
  useEffect(() => { fmsRequest<any>(wholeSet("customers/")).then(payload => setCustomersList(asList(payload))).catch(() => undefined); }, []);
  useEffect(() => { fmsRequest<any>(wholeSet("places/")).then(payload => setPlacesList(asList(payload))).catch(() => undefined); }, []);
  useEffect(() => {
    if (!selected || !(selected.routes || []).length) { setMapData(null); return; }
    let active = true;
    fmsRequest<any>(`dispatch/plans/${selected.id}/map/`).then(payload => { if (active) setMapData(payload); }).catch(() => { if (active) setMapData(null); });
    return () => { active = false; };
  }, [selected?.id, selected?.status, (selected?.routes || []).length]);

  const openPlan = async (id: number) => {
    const payload = await fmsRequest<any>(`dispatch/plans/${id}/`);
    setSelected(payload);
  };

  const refreshSelected = async () => { if (selected) await openPlan(selected.id); };

  const createPlan = async () => {
    setBusy(true);
    try {
      const plan = await fmsRequest<any>("dispatch/plans/", { method: "POST", body: JSON.stringify({ plan_date: newDate }) });
      onAction(`Plan ${plan.code} created`);
      load();
      openPlan(plan.id);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 160) : "Could not create plan", "warn");
    } finally { setBusy(false); }
  };

  const runStep = async (path: string, message: (payload: any) => string) => {
    if (!selected) return;
    setBusy(true);
    try {
      const payload = await fmsRequest<any>(`dispatch/plans/${selected.id}/${path}/`, { method: "POST", body: "{}" });
      onAction(message(payload));
      await refreshSelected();
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 200) : "Action failed", "warn");
    } finally { setBusy(false); }
  };

  const pickStrategy = (name: string) => {
    setStrategyName(name);
    setWeightOverrides({});
    setConstraintOverrides({});
  };

  const runCollect = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      if (collectFilters.temperature_class) body.temperature_class = collectFilters.temperature_class;
      if (collectFilters.scheduled_from) body.scheduled_from = collectFilters.scheduled_from;
      if (collectFilters.scheduled_to) body.scheduled_to = collectFilters.scheduled_to;
      if (collectFilters.customers.length) body.customers = collectFilters.customers.map(Number);
      if (collectFilters.pickup_places.length) body.pickup_places = collectFilters.pickup_places.map(Number);
      if (!collectFilters.include_indents) body.include_indents = false;
      const payload = await fmsRequest<any>(`dispatch/plans/${selected.id}/collect/`, { method: "POST", body: JSON.stringify(body) });
      onAction(`${payload.task_count} task(s), ${payload.vehicle_count} vehicle(s) collected`);
      await refreshSelected();
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 200) : "Collect failed", "warn");
    } finally { setBusy(false); }
  };

  const runSolve = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = { strategy: strategyName };
      if (Object.keys(weightOverrides).length) body.weights = weightOverrides;
      if (Object.keys(constraintOverrides).length) body.constraints = constraintOverrides;
      const payload = await fmsRequest<any>(`dispatch/plans/${selected.id}/solve/`, { method: "POST", body: JSON.stringify(body) });
      onAction(payload.solver_status || "Solved");
      await refreshSelected();
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 200) : "Solve failed", "warn");
    } finally { setBusy(false); }
  };

  const routeSortValue = (route: any, key: string) => key.split(".").reduce((value, part) => value?.[part], route);
  const toggleRouteSort = (key: string) => setRouteSort(prev => prev.key === key ? { key, dir: (prev.dir * -1) as 1 | -1 } : { key, dir: 1 });
  const sortArrow = (key: string) => routeSort.key === key ? (routeSort.dir === 1 ? " ▲" : " ▼") : "";
  const sortedRoutes = [...(selected?.routes || [])].sort((a, b) => {
    const av = routeSortValue(a, routeSort.key);
    const bv = routeSortValue(b, routeSort.key);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return av > bv ? routeSort.dir : av < bv ? -routeSort.dir : 0;
  });

  const moveTask = async (taskId: number, toRouteId: string) => {
    if (!selected || !toRouteId) return;
    setBusy(true);
    try {
      await fmsRequest<any>(`dispatch/plans/${selected.id}/move-task/`, { method: "POST", body: JSON.stringify({ task: taskId, to_route: Number(toRouteId) }) });
      onAction("Task moved to another route");
      await refreshSelected();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 160) : "Could not move task", "warn");
    } finally { setBusy(false); }
  };

  const unrouteTask = async (taskId: number) => {
    if (!selected) return;
    setBusy(true);
    try {
      await fmsRequest<any>(`dispatch/plans/${selected.id}/unroute-task/`, { method: "POST", body: JSON.stringify({ task: taskId }) });
      onAction("Task taken off its route - available for the next solve");
      await refreshSelected();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 160) : "Could not unroute task", "warn");
    } finally { setBusy(false); }
  };

  const [compareNames, setCompareNames] = useState<string[]>([]);
  const [scenarios, setScenarios] = useState<any[] | null>(null);

  const toggleCompareName = (name: string) =>
    setCompareNames(prev => prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]);

  const runCompare = async () => {
    if (!selected || compareNames.length < 2) return;
    setBusy(true);
    try {
      const payload = await fmsRequest<any>(`dispatch/plans/${selected.id}/compare/`, { method: "POST", body: JSON.stringify({ strategies: compareNames }) });
      setScenarios(payload.scenarios);
      onAction(`${payload.scenarios.length} scenario(s) solved for comparison`);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 200) : "Compare failed", "warn");
    } finally { setBusy(false); }
  };

  const adoptScenario = async (scenarioId: number) => {
    if (!selected) return;
    setBusy(true);
    try {
      await fmsRequest<any>(`dispatch/plans/${selected.id}/adopt/`, { method: "POST", body: JSON.stringify({ scenario: scenarioId }) });
      onAction("Scenario adopted - its routes now belong to this plan");
      setScenarios(null);
      await refreshSelected();
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 200) : "Adopt failed", "warn");
    } finally { setBusy(false); }
  };

  const assignDriver = async (routeId: number, driverId: string) => {
    if (!driverId) return;
    setBusy(true);
    try {
      await fmsRequest<any>(`dispatch/routes/${routeId}/assign-driver/`, { method: "POST", body: JSON.stringify({ driver: Number(driverId) }) });
      onAction("Driver assigned to route");
      await refreshSelected();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 160) : "Could not assign driver", "warn");
    } finally { setBusy(false); }
  };

  const summary = selected?.summary || {};

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">CVRP DISPATCH PLANNING</p><h2>Route planning</h2><p>Collect tomorrow's demand, solve a costed plan across own dry, own reefer and hired capacity, then commit it.</p></div></div>

    <section className="module-table-card">
      <div className="module-toolbar">
        <div><strong>Plans</strong><span>{plans.length} records</span></div>
        <div className="assign-row">
          <input type="date" value={newDate} onChange={event => setNewDate(event.target.value)} />
          <button className="primary" disabled={busy} onClick={createPlan}>＋ New plan</button>
          <button onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {loading ? <div className="data-state">Loading plans…</div> : !plans.length ? <div className="data-state">No dispatch plans yet. Create one for tomorrow's demand.</div> :
      <div className="table-wrap"><table><thead><tr><th>Plan</th><th>Date</th><th>Status</th><th>Fill rate</th><th>Margin</th></tr></thead>
        <tbody>{plans.map(plan => <tr key={plan.id} className="clickable" onClick={() => openPlan(plan.id)}>
          <td><strong>{plan.code}</strong></td>
          <td>{plan.plan_date}</td>
          <td><span className={"status " + plan.status}>{planStatusLabel[plan.status] || plan.status}</span></td>
          <td>{plan.summary?.fill_rate_percent != null ? `${plan.summary.fill_rate_percent}%` : "—"}</td>
          <td>{plan.summary?.total_margin != null ? rupees(plan.summary.total_margin) : "—"}</td>
        </tr>)}</tbody></table></div>}
    </section>

    {selected && <div className="record-backdrop" onMouseDown={() => setSelected(null)}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">{selected.code}</p><h2>Plan for {selected.plan_date}</h2><span className={"status " + selected.status}>{planStatusLabel[selected.status] || selected.status}</span></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>

      <div className="record-actions">
        <button className="secondary" disabled={busy || ["committed", "superseded"].includes(selected.status)} onClick={runCollect}>Collect demand</button>
        <button className="primary" disabled={busy || selected.status !== "solved"} onClick={() => runStep("commit", p => `${p.committed_routes?.length || 0} route(s) committed`)}>Commit plan</button>
      </div>

      {!["committed", "superseded"].includes(selected.status) && <div className="record-section strategy-panel">
        <p className="eyebrow">COLLECT DEMAND</p>
        <button className="link-toggle" type="button" onClick={() => setShowCollectFilters(!showCollectFilters)}>
          {showCollectFilters ? "▾ Hide filters" : "▸ Narrow what gets collected"}
        </button>
        {showCollectFilters && <div className="strategy-advanced">
          <div className="strategy-constraint-grid">
            <label><span>Temperature</span>
              <select value={collectFilters.temperature_class} onChange={event => setCollectFilters(prev => ({ ...prev, temperature_class: event.target.value }))}>
                <option value="">Any</option>
                <option value="dry">Dry</option>
                <option value="chiller">Chiller</option>
                <option value="frozen">Frozen</option>
              </select>
            </label>
            <label className="checkbox-field">
              <input type="checkbox" checked={collectFilters.include_indents}
                     onChange={event => setCollectFilters(prev => ({ ...prev, include_indents: event.target.checked }))} />
              <span>Include open indents (not just booked orders)</span>
            </label>
            <label><span>Scheduled from</span>
              <input type="datetime-local" value={collectFilters.scheduled_from}
                     onChange={event => setCollectFilters(prev => ({ ...prev, scheduled_from: event.target.value }))} />
            </label>
            <label><span>Scheduled to</span>
              <input type="datetime-local" value={collectFilters.scheduled_to}
                     onChange={event => setCollectFilters(prev => ({ ...prev, scheduled_to: event.target.value }))} />
            </label>
          </div>
          <label><span>Customers (leave empty for all)</span>
            <select multiple value={collectFilters.customers} size={4}
                    onChange={event => setCollectFilters(prev => ({ ...prev, customers: Array.from(event.target.selectedOptions, o => o.value) }))}>
              {customersList.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label><span>Pickup places (leave empty for all)</span>
            <select multiple value={collectFilters.pickup_places} size={4}
                    onChange={event => setCollectFilters(prev => ({ ...prev, pickup_places: Array.from(event.target.selectedOptions, o => o.value) }))}>
              {placesList.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        </div>}
      </div>}

      {!["committed", "superseded"].includes(selected.status) && <div className="record-section strategy-panel">
        <p className="eyebrow">RUN PLAN</p>
        <div className="strategy-cards">
          {(strategies.length ? strategies : [{ name: "balanced", label: "Balanced", description: "Loading strategies…" }]).map(s => (
            <button key={s.name} type="button" className={"strategy-card" + (strategyName === s.name ? " active" : "")} onClick={() => pickStrategy(s.name)}>
              <strong>{s.label}</strong>
              <span>{s.description}</span>
            </button>
          ))}
        </div>
        <button className="link-toggle" type="button" onClick={() => setShowAdvanced(!showAdvanced)}>
          {showAdvanced ? "▾ Hide advanced weights" : "▸ Advanced weights & constraints"}
        </button>
        {showAdvanced && <div className="strategy-advanced">
          <div className="strategy-weight-grid">
            {Object.entries(strategies.find(s => s.name === strategyName)?.weights || {}).map(([key, value]) => (
              <label key={key}>
                <span>{key.replace(/_/g, " ")}</span>
                <input type="number" step="0.1" value={weightOverrides[key] ?? (value as number)}
                       onChange={event => setWeightOverrides(prev => ({ ...prev, [key]: Number(event.target.value) }))} />
              </label>
            ))}
          </div>
          <div className="strategy-constraint-grid">
            <label><span>Max outsource %</span>
              <input type="number" placeholder="no limit" value={constraintOverrides.max_outsource_percent ?? ""}
                     onChange={event => setConstraintOverrides(prev => ({ ...prev, max_outsource_percent: event.target.value === "" ? null : Number(event.target.value) }))} />
            </label>
            <label><span>Min utilisation %</span>
              <input type="number" placeholder="no minimum" value={constraintOverrides.min_utilisation_percent ?? ""}
                     onChange={event => setConstraintOverrides(prev => ({ ...prev, min_utilisation_percent: event.target.value === "" ? null : Number(event.target.value) }))} />
            </label>
            <label><span>Delivery windows</span>
              <select value={constraintOverrides.time_windows ?? (strategies.find(s => s.name === strategyName)?.constraints?.time_windows || "soft")}
                      onChange={event => setConstraintOverrides(prev => ({ ...prev, time_windows: event.target.value }))}>
                <option value="soft">Soft — penalise a late arrival</option>
                <option value="hard">Hard — never plan a late arrival</option>
              </select>
            </label>
            <label className="checkbox-field">
              <input type="checkbox" checked={constraintOverrides.allow_partial_service ?? true}
                     onChange={event => setConstraintOverrides(prev => ({ ...prev, allow_partial_service: event.target.checked }))} />
              <span>Allow partial service — outsource or drop what does not fit rather than fail the solve</span>
            </label>
          </div>
        </div>}
        <button className="primary" disabled={busy} onClick={runSolve}>
          Run plan — {strategies.find(s => s.name === strategyName)?.label || "Balanced"}
        </button>

        <div className="compare-panel">
          <p className="eyebrow">COMPARE STRATEGIES</p>
          <div className="compare-chips">
            {strategies.map(s => <label key={s.name} className="compare-chip">
              <input type="checkbox" checked={compareNames.includes(s.name)} onChange={() => toggleCompareName(s.name)} />
              <span>{s.label}</span>
            </label>)}
          </div>
          <button className="secondary" disabled={busy || compareNames.length < 2} onClick={runCompare}>
            Solve {compareNames.length || ""} ways and compare
          </button>

          {!!scenarios?.length && <div className="table-wrap" style={{ marginTop: 10 }}>
            <table>
              <thead><tr><th>Strategy</th><th>Fill rate</th><th>Margin</th><th>Own vs hire</th><th>On-time</th><th></th></tr></thead>
              <tbody>{scenarios.map(s => <tr key={s.id}>
                <td>{strategies.find(x => x.name === s.strategy)?.label || s.strategy}</td>
                <td>{s.summary?.fill_rate_percent}%</td>
                <td className={Number(s.summary?.total_margin) >= 0 ? "good" : "bad"}>{rupees(s.summary?.total_margin)}</td>
                <td>{s.summary?.own_vs_hire_percent != null ? `${s.summary.own_vs_hire_percent}% own` : "—"}</td>
                <td>{s.summary?.projected_on_time_percent != null ? `${s.summary.projected_on_time_percent}%` : "—"}</td>
                <td><button className="row-action" disabled={busy} onClick={() => adoptScenario(s.id)}>Adopt</button></td>
              </tr>)}</tbody>
            </table>
          </div>}
        </div>
      </div>}

      {!!Object.keys(summary).length && <div className="record-section">
        <p className="eyebrow">PLAN SUMMARY</p>
        {summary.strategy && <p className="strategy-chip">Solved with <strong>{strategies.find(s => s.name === summary.strategy)?.label || summary.strategy}</strong></p>}

        <div className="kpi-tiles">
          <div className="kpi-tile"><span>Fill rate</span><strong>{summary.fill_rate_percent}%</strong></div>
          <div className="kpi-tile"><span>Margin</span><strong className={Number(summary.total_margin) >= 0 ? "good" : "bad"}>{rupees(summary.total_margin)}</strong></div>
          <div className="kpi-tile"><span>Cost / tonne-km</span><strong>{summary.cost_per_tonne_km != null ? `₹${summary.cost_per_tonne_km}` : "—"}</strong></div>
          <div className="kpi-tile"><span>Projected on-time</span><strong>{summary.projected_on_time_percent != null ? `${summary.projected_on_time_percent}%` : "—"}</strong></div>
        </div>

        <div className="tracking-grid kpi-secondary">
          <div><span>Total tasks</span><strong>{summary.total_tasks}</strong></div>
          <div><span>Served (own fleet)</span><strong>{summary.served_own_fleet}</strong></div>
          <div><span>Outsourced</span><strong>{summary.outsourced}</strong></div>
          <div><span>Unroutable</span><strong>{summary.dropped_unroutable}</strong></div>
          <div><span>Routes used</span><strong>{summary.routes_used}</strong></div>
          <div><span>Distance (dead km)</span><strong>{summary.total_distance_km} km <small>({summary.dead_km_percent}% dead)</small></strong></div>
          <div><span>Revenue</span><strong>{rupees(summary.total_revenue)}</strong></div>
          <div><span>Cost</span><strong>{rupees(summary.total_cost)}</strong></div>
          <div><span>Own fleet value</span><strong>{rupees(summary.own_fleet_value)}</strong></div>
          <div><span>Outsourced value</span><strong>{rupees(summary.outsourced_value)}</strong></div>
          <div><span>Own vs hire</span><strong>{summary.own_vs_hire_percent != null ? `${summary.own_vs_hire_percent}% own` : "—"}</strong></div>
          <div><span>Avg weight fill</span><strong>{summary.avg_weight_utilisation}%</strong></div>
          <div><span>Avg volume fill</span><strong>{summary.avg_volume_utilisation != null ? `${summary.avg_volume_utilisation}%` : "not tracked"}</strong></div>
          <div><span>Stops / route</span><strong>{summary.stops_per_route}</strong></div>
          <div><span>Avg route duration</span><strong>{summary.avg_route_duration_hours} h</strong></div>
          <div><span>By temperature</span><strong>{Object.entries(summary.tasks_by_temperature || {}).map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}</strong></div>
          <div><span>Deferred / Held for review</span><strong>{summary.deferred_count ?? 0} / {summary.held_for_review_count ?? 0}</strong></div>
          <div><span>By scenario profile</span><strong>{Object.entries(summary.scenario_breakdown || {}).map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}</strong></div>
        </div>

        {!!(summary.constraint_breaches || []).length && <div className="constraint-breaches">
          {summary.constraint_breaches.map((breach: string, index: number) => <p key={index} className="breach-line">⚠ {breach}</p>)}
        </div>}
      </div>}

      {!!(selected.routes || []).length && <div className="record-section">
        <p className="eyebrow">ROUTE MAP</p>
        {mapData
          ? <PlanMap routes={mapData.routes} unroutedTasks={mapData.unrouted_tasks} highlightRoute={expandedRoute}
                     onSelectRoute={id => setExpandedRoute(id)} />
          : <div className="data-state">Loading map…</div>}
      </div>}

      {!!(selected.routes || []).length && <div className="record-section">
        <p className="eyebrow">ROUTES</p>
        <div className="table-wrap">
          <table className="kpi-route-table">
            <thead><tr>
              <th className="sortable" onClick={() => toggleRouteSort("plan_vehicle_detail.registration_number")}>Vehicle{sortArrow("plan_vehicle_detail.registration_number")}</th>
              <th className="sortable" onClick={() => toggleRouteSort("total_distance_km")}>Distance{sortArrow("total_distance_km")}</th>
              <th className="sortable" onClick={() => toggleRouteSort("dead_km_percent")}>Dead km{sortArrow("dead_km_percent")}</th>
              <th>Utilisation</th>
              <th className="sortable" onClick={() => toggleRouteSort("estimated_margin")}>Margin{sortArrow("estimated_margin")}</th>
              <th className="sortable" onClick={() => toggleRouteSort("orders_carried")}>Orders{sortArrow("orders_carried")}</th>
              <th>On-time</th>
            </tr></thead>
            <tbody>{sortedRoutes.map((route: any) => <tr key={route.id} className="clickable" onClick={() => setExpandedRoute(expandedRoute === route.id ? null : route.id)}>
              <td>{route.plan_vehicle_detail?.registration_number || "Route #" + route.sequence}</td>
              <td>{route.total_distance_km} km</td>
              <td>{route.dead_km_percent}%</td>
              <td><div className="util-bar"><i style={{ width: `${Math.min(100, route.avg_utilisation_percent ?? 0)}%` }} /></div></td>
              <td className={Number(route.estimated_margin) >= 0 ? "good" : "bad"}>{rupees(route.estimated_margin)}</td>
              <td>{route.orders_carried}</td>
              <td>{route.on_time_stops ? `${route.on_time_stops.on_time}/${route.on_time_stops.total}` : "—"}</td>
            </tr>)}</tbody>
          </table>
        </div>

        {(selected.routes || []).filter((route: any) => expandedRoute === route.id).map((route: any) => <div key={route.id} className="record-field" style={{ display: "block", marginTop: 12 }}>
          <div><strong>{route.plan_vehicle_detail?.registration_number || "Route #" + route.sequence}</strong> · {rupees(route.estimated_cost)} cost · {route.stop_count} stop(s) · {route.window_risk_stops} at risk of a late window</div>

          <div className="gantt">
            <div className="gantt-row">
              <span className="gantt-label">Drive / wait</span>
              <div className="gantt-track">
                <div className="seg-drive" style={{ width: `${route.total_duration_minutes ? (route.drive_minutes / route.total_duration_minutes * 100) : 0}%` }} />
                <div className="seg-wait" style={{ width: `${route.total_duration_minutes ? (route.wait_minutes / route.total_duration_minutes * 100) : 0}%` }} />
              </div>
              <span className="gantt-total">{route.total_duration_minutes ? (route.total_duration_minutes / 60).toFixed(1) : 0}h</span>
            </div>
            <div className="gantt-legend">
              <span><i className="seg-drive" />Drive {route.drive_minutes}m</span>
              <span><i className="seg-wait" />Wait {route.wait_minutes}m</span>
            </div>
          </div>

          <div className="load-profile" title="Load after each stop, against vehicle capacity">
            {(route.stops || []).map((stop: any) => <div key={stop.id} className="load-bar" title={`${stop.place_name}: ${stop.load_after_kg}kg`}>
              <i style={{ height: `${route.plan_vehicle_detail?.capacity_kg ? Math.min(100, Number(stop.load_after_kg) / Number(route.plan_vehicle_detail.capacity_kg) * 100) : 0}%` }} />
            </div>)}
          </div>

          <div className="stop-override-list" style={{ marginTop: 10 }}>
            {(route.stops || []).map((stop: any) => <div key={stop.id} className="stop-override-row">
              <span>{stop.stop_type === "pickup" ? "▲" : "▼"} {stop.place_name} ({stop.load_after_kg}kg){stop.order_number ? ` · ${stop.order_number}` : ""}</span>
              {stop.stop_type === "drop" && stop.task_id && !route.committed_trip && <span className="stop-override-actions">
                <select defaultValue="" onChange={event => { moveTask(stop.task_id, event.target.value); event.target.value = ""; }}>
                  <option value="">Move to…</option>
                  {(selected.routes || []).filter((r: any) => r.id !== route.id).map((r: any) =>
                    <option key={r.id} value={r.id}>{r.plan_vehicle_detail?.registration_number || "Route #" + r.sequence}</option>)}
                </select>
                <button className="row-action" disabled={busy} onClick={() => unrouteTask(stop.task_id)}>Unroute</button>
              </span>}
            </div>)}
          </div>
          {!route.plan_vehicle_detail?.driver && !route.committed_trip && <div className="assign-row" style={{ marginTop: 6 }}>
            <select onChange={event => assignDriver(route.id, event.target.value)} defaultValue="">
              <option value="">Assign a driver…</option>
              {drivers.map(d => <option key={d.id} value={d.id}>{d.name} · {d.phone}</option>)}
            </select>
          </div>}
        </div>)}
      </div>}

      {!!(selected.hire_requirements || []).length && <div className="record-section">
        <p className="eyebrow">CAPACITY TO BUY (OUTSOURCED)</p>
        {(selected.hire_requirements || []).map((req: any) => <div key={req.id} className="record-field">
          <span>{req.pickup_name} → {req.dropoff_name}</span>
          <strong>{req.capacity_kg} kg · {rupees(req.estimated_cost)} · {req.task_count} task(s) · {req.status}</strong>
        </div>)}
      </div>}
    </aside></div>}
  </div>;
}

function ScenarioProfilesView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [profiles, setProfiles] = useState<any[]>([]);
  const [strategies, setStrategies] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [previewPlan, setPreviewPlan] = useState("");
  const [previewResult, setPreviewResult] = useState<any>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  const load = () => { setLoading(true); fmsRequest<any>(wholeSet("dispatch/scenario-profiles/")).then(payload => setProfiles(asList(payload))).finally(() => setLoading(false)); };
  useEffect(load, [reloadKey]);
  useEffect(() => { fmsRequest<any>("dispatch/strategies/").then(payload => setStrategies(asList(payload))).catch(() => undefined); }, []);
  useEffect(() => { fmsRequest<any>(wholeSet("dispatch/plans/")).then(payload => setPlans(asList(payload))).catch(() => undefined); }, [reloadKey]);

  const blank = () => ({
    name: "", scenario_type: "custom", description: "", priority: 100, active: true,
    match_temperature_classes: [] as string[], match_min_distance_km: "", match_max_distance_km: "",
    match_min_drops: "", match_same_city_only: false,
    base_strategy: "balanced", weight_overrides: {} as Record<string, number>, constraint_overrides: {} as Record<string, any>,
    fallback_action: "outsource", fallback_profile: "",
  });

  const open = (profile: any | null) => {
    setError(""); setPreviewResult(null); setPreviewPlan("");
    setEditing(profile ? {
      ...profile,
      match_min_distance_km: profile.match_min_distance_km ?? "", match_max_distance_km: profile.match_max_distance_km ?? "",
      match_min_drops: profile.match_min_drops ?? "", fallback_profile: profile.fallback_profile ?? "",
      weight_overrides: profile.weight_overrides || {}, constraint_overrides: profile.constraint_overrides || {},
      match_temperature_classes: profile.match_temperature_classes || [],
    } : blank());
  };

  const setField = (key: string, value: any) => setEditing((prev: any) => ({ ...prev, [key]: value }));

  const toggleTemperature = (cls: string) => setEditing((prev: any) => ({
    ...prev,
    match_temperature_classes: prev.match_temperature_classes.includes(cls)
      ? prev.match_temperature_classes.filter((c: string) => c !== cls)
      : [...prev.match_temperature_classes, cls],
  }));

  const setWeightOverride = (key: string, value: string) => setEditing((prev: any) => {
    const next = { ...prev.weight_overrides };
    if (value === "") delete next[key]; else next[key] = Number(value);
    return { ...prev, weight_overrides: next };
  });

  const setConstraintOverride = (key: string, value: any) => setEditing((prev: any) => {
    const next = { ...prev.constraint_overrides };
    if (value === "" || value === null) delete next[key]; else next[key] = value;
    return { ...prev, constraint_overrides: next };
  });

  const save = async () => {
    setBusy(true); setError("");
    try {
      const body = {
        ...editing,
        match_min_distance_km: editing.match_min_distance_km === "" ? null : Number(editing.match_min_distance_km),
        match_max_distance_km: editing.match_max_distance_km === "" ? null : Number(editing.match_max_distance_km),
        match_min_drops: editing.match_min_drops === "" ? null : Number(editing.match_min_drops),
        fallback_profile: editing.fallback_action === "relax" && editing.fallback_profile ? Number(editing.fallback_profile) : null,
      };
      if (editing.id) {
        await fmsRequest(`dispatch/scenario-profiles/${editing.id}/`, { method: "PATCH", body: JSON.stringify(body) });
        onAction(`${editing.name} updated`);
      } else {
        await fmsRequest("dispatch/scenario-profiles/", { method: "POST", body: JSON.stringify(body) });
        onAction(`${editing.name} created`);
      }
      setEditing(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message.slice(0, 240) : "Could not save this profile");
    } finally { setBusy(false); }
  };

  const remove = async (profile: any) => {
    if (!confirm(`Delete "${profile.name}"? Tasks it already shaped keep their record of it.`)) return;
    try { await fmsRequest(`dispatch/scenario-profiles/${profile.id}/`, { method: "DELETE" }); onAction(`${profile.name} deleted`); load(); }
    catch (e) { onAction(e instanceof Error ? e.message.slice(0, 160) : "Could not delete", "warn"); }
  };

  const runPreview = async () => {
    if (!editing?.id || !previewPlan) return;
    setPreviewBusy(true);
    try {
      const payload = await fmsRequest<any>(`dispatch/scenario-profiles/${editing.id}/preview/?plan=${previewPlan}`);
      setPreviewResult(payload);
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 160) : "Preview failed", "warn");
    } finally { setPreviewBusy(false); }
  };

  const matchSummary = (profile: any) => {
    const bits: string[] = [];
    if ((profile.match_temperature_classes || []).length) bits.push(profile.match_temperature_classes.join("/"));
    if (profile.match_min_drops) bits.push(`≥${profile.match_min_drops} drops`);
    if (profile.match_min_distance_km != null) bits.push(`≥${profile.match_min_distance_km}km`);
    if (profile.match_max_distance_km != null) bits.push(`≤${profile.match_max_distance_km}km`);
    if (profile.match_same_city_only) bits.push("same city");
    return bits.length ? bits.join(", ") : "any load";
  };

  const basePreset = strategies.find(s => s.name === editing?.base_strategy) || strategies.find(s => s.name === "balanced") || { weights: {}, constraints: {} };

  return <div className="module-page">
    <div className="module-title">
      <div>
        <p className="eyebrow">DISPATCH PLANNING</p>
        <h2>Scenario profiles</h2>
        <p>Planning and fallback logic per operational pattern — milk run, long haul, reefer, local delivery, or a pattern of your own. The next solve matches these automatically, per pickup, narrowing whatever strategy the plan itself runs under.</p>
      </div>
      <button className="primary module-action" onClick={() => open(null)}>＋ New profile</button>
    </div>

    <section className="module-table-card">
      <div className="table-wrap"><table>
        <thead><tr><th>Name</th><th>Type</th><th>Priority</th><th>Matches</th><th>Base strategy</th><th>Fallback</th><th>Status</th><th></th></tr></thead>
        <tbody>{profiles.map(profile => <tr key={profile.id} className="clickable" onClick={() => open(profile)}>
          <td><strong>{profile.name}</strong>{profile.description && <small style={{ display: "block", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis" }}>{profile.description}</small>}</td>
          <td>{String(profile.scenario_type).replaceAll("_", " ")}</td>
          <td>{profile.priority}</td>
          <td>{matchSummary(profile)}</td>
          <td>{strategies.find(s => s.name === profile.base_strategy)?.label || profile.base_strategy}</td>
          <td>{profile.fallback_action === "relax" ? `relax → ${profile.fallback_profile_name || "—"}` : profile.fallback_action}</td>
          <td><span className={"status " + (profile.active ? "active" : "inactive")}>{profile.active ? "active" : "inactive"}</span></td>
          <td><button className="row-action" onClick={event => { event.stopPropagation(); remove(profile); }}>Delete</button></td>
        </tr>)}</tbody>
      </table></div>
      {!loading && !profiles.length && <div className="data-state">No scenario profiles yet — every load plans under the plan's own strategy alone. Run <code>python manage.py seed_scenario_profiles</code> for starter profiles, or add one.</div>}
    </section>

    {editing && <div className="modal-backdrop" onMouseDown={() => setEditing(null)}><section className="action-panel" onMouseDown={event => event.stopPropagation()}>
      <div className="panel-head"><div><p className="eyebrow">{editing.id ? "EDIT PROFILE" : "NEW PROFILE"}</p><h2>{editing.name || "New scenario profile"}</h2></div><button className="panel-close" onClick={() => setEditing(null)}>×</button></div>
      <div className="action-form">
        <div className="form-grid">
          <label>Name<input value={editing.name} onChange={event => setField("name", event.target.value)} placeholder="e.g. Reefer" /></label>
          <label>Type<select value={editing.scenario_type} onChange={event => setField("scenario_type", event.target.value)}>
            <option value="milk_run">Milk run</option>
            <option value="long_haul">Long haul</option>
            <option value="reefer">Reefer</option>
            <option value="local_delivery">Local delivery</option>
            <option value="custom">Custom</option>
          </select></label>
          <label>Priority<input type="number" value={editing.priority} onChange={event => setField("priority", Number(event.target.value))} placeholder="100" /></label>
          <label className="checkbox-field" style={{ marginTop: 22 }}>
            <input type="checkbox" checked={editing.active} onChange={event => setField("active", event.target.checked)} />
            <span>Active — matched by the next solve</span>
          </label>
        </div>
        <label>Description<textarea value={editing.description} onChange={event => setField("description", event.target.value)} placeholder="What this profile is for, and why" /></label>

        <p className="eyebrow" style={{ marginTop: 6 }}>MATCHING CRITERIA — every criterion set below must hold; leave blank to match anything</p>
        <div className="form-grid">
          <label><span>Temperature class</span>
            <div className="chip-list">
              {["dry", "chiller", "frozen"].map(cls => <button type="button" key={cls}
                  className={editing.match_temperature_classes.includes(cls) ? "chip active" : "chip"}
                  onClick={() => toggleTemperature(cls)}>{cls}</button>)}
            </div>
          </label>
          <label className="checkbox-field" style={{ marginTop: 22 }}>
            <input type="checkbox" checked={editing.match_same_city_only} onChange={event => setField("match_same_city_only", event.target.checked)} />
            <span>Pickup and every drop share a city</span>
          </label>
          <label>Min drops on one pickup<input type="number" min={0} value={editing.match_min_drops} onChange={event => setField("match_min_drops", event.target.value)} placeholder="any" /></label>
          <label>Min distance to furthest drop (km)<input type="number" min={0} value={editing.match_min_distance_km} onChange={event => setField("match_min_distance_km", event.target.value)} placeholder="any" /></label>
          <label>Max distance to furthest drop (km)<input type="number" min={0} value={editing.match_max_distance_km} onChange={event => setField("match_max_distance_km", event.target.value)} placeholder="any" /></label>
        </div>

        <p className="eyebrow" style={{ marginTop: 6 }}>PLANNING LOGIC — only the values you set below are merged onto the plan's own strategy for a matched load; everything else still follows whatever strategy the plan is solved with</p>
        <div className="form-grid">
          <label>Base strategy (for reference — set the overrides below to actually change behaviour)<select value={editing.base_strategy} onChange={event => setField("base_strategy", event.target.value)}>
            {(strategies.length ? strategies : [{ name: "balanced", label: "Balanced" }]).map(s => <option key={s.name} value={s.name}>{s.label}</option>)}
          </select></label>
        </div>
        <div className="strategy-weight-grid">
          {Object.keys(basePreset.weights || {}).map(key => <label key={key}>
            <span>{key.replace(/_/g, " ")}</span>
            <input type="number" step="0.1" placeholder={String((basePreset.weights as any)[key])} value={editing.weight_overrides[key] ?? ""}
                   onChange={event => setWeightOverride(key, event.target.value)} />
          </label>)}
        </div>
        <div className="strategy-constraint-grid">
          <label><span>Max stops / route</span>
            <input type="number" placeholder="vehicle default" value={editing.constraint_overrides.max_stops_per_route ?? ""}
                   onChange={event => setConstraintOverride("max_stops_per_route", event.target.value === "" ? null : Number(event.target.value))} />
          </label>
          <label><span>Max route km</span>
            <input type="number" placeholder="vehicle default" value={editing.constraint_overrides.max_route_km ?? ""}
                   onChange={event => setConstraintOverride("max_route_km", event.target.value === "" ? null : Number(event.target.value))} />
          </label>
          <label><span>Max duty minutes</span>
            <input type="number" placeholder="vehicle default" value={editing.constraint_overrides.max_duty_minutes ?? ""}
                   onChange={event => setConstraintOverride("max_duty_minutes", event.target.value === "" ? null : Number(event.target.value))} />
          </label>
          <label><span>Delivery windows</span>
            <select value={editing.constraint_overrides.time_windows ?? ""} onChange={event => setConstraintOverride("time_windows", event.target.value || null)}>
              <option value="">plan default</option>
              <option value="soft">Soft — penalise a late arrival</option>
              <option value="hard">Hard — never plan a late arrival</option>
            </select>
          </label>
        </div>

        <p className="eyebrow" style={{ marginTop: 6 }}>FALLBACK — when a matched load cannot be placed on any route</p>
        <div className="form-grid">
          <label>Fallback action<select value={editing.fallback_action} onChange={event => setField("fallback_action", event.target.value)}>
            <option value="outsource">Buy on the spot market</option>
            <option value="relax">Retry once under a fallback profile</option>
            <option value="defer">Defer to the next plan</option>
            <option value="hold">Hold for manual review</option>
          </select></label>
          {editing.fallback_action === "relax" && <label>Fallback profile
            <select value={editing.fallback_profile} onChange={event => setField("fallback_profile", event.target.value)}>
              <option value="">Select a profile…</option>
              {profiles.filter(p => p.id !== editing.id).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>}
        </div>

        {editing.id && <div className="compare-panel">
          <p className="eyebrow">PREVIEW AGAINST A PLAN</p>
          <div className="form-grid">
            <label>Plan<select value={previewPlan} onChange={event => setPreviewPlan(event.target.value)}>
              <option value="">Select a plan…</option>
              {plans.map(plan => <option key={plan.id} value={plan.id}>{plan.code} — {plan.plan_date}</option>)}
            </select></label>
          </div>
          <button className="secondary" type="button" disabled={!previewPlan || previewBusy} onClick={runPreview}>
            {previewBusy ? "Checking…" : "Preview matching loads"}
          </button>
          {previewResult && <p style={{ marginTop: 10, fontSize: 11 }}>
            Matches <strong>{previewResult.matched_clusters}</strong> load(s) on that plan's pending demand — <strong>{previewResult.matched_tasks}</strong> task(s) total.
            {!!previewResult.sample?.length && <span> E.g. {previewResult.sample.slice(0, 3).map((s: any) => `${s.pickup} (${s.drops} drop${s.drops === 1 ? "" : "s"})`).join(", ")}.</span>}
          </p>}
        </div>}

        {error && <div className="form-error">{error}</div>}
        <div className="form-actions">
          <button type="button" className="secondary" onClick={() => setEditing(null)}>Cancel</button>
          <button className="primary" disabled={busy || !editing.name} onClick={save}>{busy ? "Saving…" : editing.id ? "Save changes" : "Create profile"}</button>
        </div>
      </div>
    </section></div>}
  </div>;
}

function HiresView({ reloadKey, onAction, openAction }: { reloadKey: number; onAction: Notify; openAction: (type: string) => void }) {
  const [hires, setHires] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<any>(null);
  const [payable, setPayable] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("hires/")).then(payload => {
      const records = asList(payload);
      setHires(records);
      setTotal(asCount(payload, records));
    }).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  useEffect(() => {
    if (!selected) { setPayable(null); return; }
    let active = true;
    fmsRequest<any>(`hires/${selected.id}/payable/`).then(payload => { if (active) setPayable(payload); }).catch(() => { if (active) setPayable(null); });
    return () => { active = false; };
  }, [selected?.id, selected?.status]);

  const call = async (path: string, message: (payload: any) => string) => {
    setBusy(true);
    try {
      const payload = await fmsRequest<any>(`hires/${selected.id}/${path}/`, { method: "POST", body: "{}" });
      onAction(message(payload));
      const updated = await fmsRequest<any>(`hires/${selected.id}/`);
      setSelected(updated);
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Action failed", "warn");
    } finally { setBusy(false); }
  };

  const pending = hires.filter(hire => !["settled", "cancelled"].includes(hire.status));
  const totalPayable = hires.reduce((sum, hire) => sum + Number(hire.agreed_rate || 0), 0);

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">OUTSIDE-SOURCED CAPACITY</p><h2>Vendor hires</h2><p>The commercial terms agreed with a transport owner for one outside-sourced trip, and its settlement.</p></div><div className="assign-row"><button className="secondary module-action" onClick={() => openAction("vendor-lane-rate")}>＋ Lane rate</button><button className="primary module-action" onClick={() => openAction("hire")}>＋ Add hire</button></div></div>
    <div className="module-stats">
      <div className="module-stat"><span>Total hires</span><strong>{loading ? "—" : total}</strong><small>Across all statuses</small></div>
      <div className="module-stat"><span>Open</span><strong>{loading ? "—" : pending.length}</strong><small>Not yet settled or cancelled</small></div>
      <div className="module-stat"><span>Agreed value</span><strong>{rupees(totalPayable)}</strong><small>Sum of agreed rates</small></div>
    </div>
    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>All hires</strong><span>{hires.length} records</span></div><button onClick={load}>↻ Refresh</button></div>
      {loading ? <div className="data-state">Loading hires…</div> : !hires.length ? <div className="data-state">No vendor hires yet. Confirm a spot hire from an order, or add one here.</div> :
      <div className="table-wrap"><table><thead><tr><th>Order</th><th>Vendor</th><th>Vehicle</th><th>Agreed rate</th><th>Basis</th><th>Status</th></tr></thead>
        <tbody>{hires.map(hire => <tr key={hire.id} className="clickable" onClick={() => setSelected(hire)}>
          <td><strong>{hire.order_number}</strong></td>
          <td>{hire.vendor_name}</td>
          <td>{hire.outside_vehicle_number || "—"}</td>
          <td>{rupees(hire.agreed_rate)}</td>
          <td>{String(hire.rate_basis).replaceAll("_", " ")}</td>
          <td><span className={"status " + hire.status}>{hireStatusLabel[hire.status] || hire.status}</span></td>
        </tr>)}</tbody></table></div>}
    </section>

    {selected && <div className="record-backdrop" onMouseDown={() => setSelected(null)}><aside className="record-drawer" onMouseDown={event => event.stopPropagation()}>
      <div className="record-head"><div><p className="eyebrow">HIRE FOR {selected.order_number}</p><h2>{selected.vendor_name}</h2><span className={"status " + selected.status}>{hireStatusLabel[selected.status] || selected.status}</span></div><button className="panel-close" onClick={() => setSelected(null)}>×</button></div>
      <div className="record-fields">
        <div className="record-field"><span>Hire type</span><strong>{selected.hire_type === "contract" ? "Contract rate" : "Spot hire"}</strong></div>
        <div className="record-field"><span>Vehicle</span><strong>{selected.outside_vehicle_number || "—"}{selected.outside_vehicle_type ? ` · ${selected.outside_vehicle_type}` : ""}</strong></div>
        <div className="record-field"><span>Driver</span><strong>{selected.driver_name || "—"}{selected.driver_phone ? ` · ${selected.driver_phone}` : ""}</strong></div>
        <div className="record-field"><span>Agreed rate</span><strong>{rupees(selected.agreed_rate)} ({String(selected.rate_basis).replaceAll("_", " ")})</strong></div>
        <div className="record-field"><span>Loading / unloading</span><strong>{rupees(selected.loading_charge)} + {rupees(selected.unloading_charge)}</strong></div>
        <div className="record-field"><span>Detention</span><strong>{rupees(selected.detention_rate_per_day)}/day, {selected.toll_responsibility === "ours" ? "toll on us" : "toll on vendor"}</strong></div>
        <div className="record-field"><span>Advance paid</span><strong>{rupees(selected.advance_amount)}</strong></div>
        <div className="record-field"><span>Payment terms</span><strong>{selected.payment_terms_days} days</strong></div>
        <div className="record-field"><span>Created</span><strong>{stamp(selected.created_at)}</strong></div>
        <div className="record-field"><span>Last updated</span><strong>{stamp(selected.updated_at)}</strong></div>
      </div>

      {payable && <div className="record-section">
        <p className="eyebrow">PAYABLE BREAKDOWN</p>
        <div className="margin-strip">
          <div><span>Gross</span><strong>{rupees(payable.gross_amount)}</strong></div>
          <div><span>TDS ({payable.tds_percent}%)</span><strong>{rupees(payable.tds_amount)}</strong></div>
          <div><span>Balance due</span><strong className={payable.balance_due > 0 ? "bad" : "good"}>{rupees(payable.balance_due)}</strong></div>
        </div>
        <div className="tracking-grid">
          <div><span>Base amount</span><strong>{rupees(payable.base_amount)}</strong></div>
          <div><span>Detention</span><strong>{rupees(payable.detention_amount)}</strong></div>
          <div><span>Extra charges</span><strong>{rupees(payable.extra_charges)}</strong></div>
          <div><span>Deductions</span><strong>{rupees(payable.deductions)}</strong></div>
          <div><span>Advance paid</span><strong>{rupees(payable.advance_amount)}</strong></div>
          <div><span>Total payable</span><strong>{rupees(payable.total_payable)}</strong></div>
        </div>
      </div>}

      <div className="record-actions">
        <button className="secondary" disabled={busy || selected.status !== "draft"} onClick={() => call("confirm", () => "Hire confirmed")}>Confirm hire</button>
        <button className="secondary" disabled={busy} onClick={() => call("send-confirmation", payload => `Vendor emailed at ${payload.to}`)}>Email vendor</button>
        <button className="primary" disabled={busy || selected.status === "cancelled"} onClick={() => call("raise-bill", payload => payload.created ? `Bill ${payload.bill_number} raised` : `Already billed on ${payload.bill_number}`)}>Raise bill</button>
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
  const [lostReason, setLostReason] = useState("");
  const [courierOnly, setCourierOnly] = useState(false);
  const [uploadUrl, setUploadUrl] = useState("");
  const [uploading, setUploading] = useState(false);

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

  const uploadDocument = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !selected) return;
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("document", file);
      const result = await fmsRequest<any>(`orders/${selected.order}/pod-document-upload/`, { method: "POST", body: formData });
      setUploadUrl(result.url || "");
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 120) : "Upload failed", "warn");
    } finally { setUploading(false); }
  };

  const capture = async (event: React.FormEvent) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    await call(`orders/${selected.order}/pod-submit/`, {
      receiver_name: String(form.get("receiver_name") || ""), otp: String(form.get("otp") || ""),
      file_url: uploadUrl || String(form.get("file_url") || ""), remarks: String(form.get("remarks") || ""),
      shortage_kg: Number(form.get("shortage_kg") || 0), damage_reported: form.get("damage_reported") === "on",
    }, "ePOD captured");
    setUploadUrl("");
  };

  const sendByCourier = async (event: React.FormEvent) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    await call(`proofs/${selected.id}/courier-dispatch/`, {
      courier_name: String(form.get("courier_name") || ""), awb_number: String(form.get("awb_number") || ""),
      expected_by: String(form.get("expected_by") || "") || undefined, remarks: String(form.get("courier_note") || ""),
    }, "Physical POD dispatch recorded");
  };
  const reportLost = async () => {
    await call(`proofs/${selected.id}/courier-lost/`, { remarks: lostReason }, "Marked lost in transit");
    setLostReason("");
  };

  const courierPending = (proof: any) => proof.physical_copy_required && proof.courier_status !== "delivered";
  const shown = (filter ? proofs.filter(proof => proof.status === filter) : proofs)
    .filter(proof => !courierOnly || courierPending(proof));
  const counts = (status: string) => proofs.filter(proof => proof.status === status).length;
  const courierPendingCount = proofs.filter(courierPending).length;
  const courierOverdueCount = proofs.filter(proof => proof.courier_overdue).length;

  return <div className="module-page">
    <div className="module-title"><div><p className="eyebrow">ELECTRONIC PROOF OF DELIVERY</p><h2>ePOD</h2><p>Issue the delivery OTP, capture what the driver hands back, and clear it for billing. A consignment cannot be invoiced until its proof is verified.</p></div></div>
    <div className="module-stats">
      <div className="module-stat"><span>Awaiting delivery</span><strong>{loading ? "—" : counts("awaiting")}</strong><small>OTP issued, truck still running</small></div>
      <div className="module-stat warn"><span>In review</span><strong>{loading ? "—" : counts("submitted")}</strong><small>Held by a shortage or damage</small></div>
      <div className="module-stat"><span>Verified</span><strong>{loading ? "—" : counts("verified")}</strong><small>Cleared for invoicing</small></div>
      <div className={"module-stat" + (courierOverdueCount ? " alert" : "")}><span>Physical copy by courier</span><strong>{loading ? "—" : courierPendingCount}</strong><small>{courierOverdueCount ? `${courierOverdueCount} overdue` : "Out with a courier"}</small></div>
    </div>
    <section className="module-table-card">
      <div className="module-toolbar"><div><strong>Delivery proofs</strong><span>{shown.length} of {proofs.length}</span></div>
        <div className="toolbar-actions wrap">{podFilters.map(([value, label]) => <button key={value} className={value === filter ? "chip active" : "chip"} onClick={() => setFilter(value)}>{label}</button>)}
          <button className={courierOnly ? "chip active" : "chip"} onClick={() => setCourierOnly(v => !v)}>Physical copy pending</button></div></div>
      <div className="table-wrap"><table><thead><tr><th>Consignment</th><th>Customer</th><th>Drop</th><th>Received by</th><th>Captured</th><th>Exception</th><th>Status</th><th>Physical copy</th></tr></thead>
        <tbody>{shown.map(proof => <tr key={proof.id} className="clickable" onClick={() => { setSelected(proof); setReason(""); setLostReason(""); }}>
          <td><strong>{proof.order_number}</strong><small>{proof.tracking_number}</small></td>
          <td>{proof.customer_name}</td>
          <td>{proof.destination || "—"}</td>
          <td>{proof.receiver_name || "—"}</td>
          <td>{proof.captured_at ? new Date(proof.captured_at).toLocaleString("en-IN") : "—"}</td>
          <td>{proof.is_clean ? "Clean" : [Number(proof.shortage_kg) ? `${Number(proof.shortage_kg)} kg short` : "", proof.damage_reported ? "damage" : ""].filter(Boolean).join(" · ")}</td>
          <td><span className={"status " + proof.status}>{proof.status}</span></td>
          <td>{proof.physical_copy_required ? <span className={"status " + (proof.courier_overdue ? "cancelled" : String(proof.courier_status).replaceAll("_", "-"))}>{proof.courier_overdue ? "overdue" : proof.courier_status.replaceAll("_", " ")}</span> : "—"}</td>
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
        <div className="record-field"><span>Created</span><strong>{stamp(selected.created_at)}</strong></div>
        <div className="record-field"><span>Last updated</span><strong>{stamp(selected.updated_at)}</strong></div>
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
          <label>Upload document
            {uploadUrl
              ? <span className="pod-upload-done">
                  <a href={uploadUrl} target="_blank" rel="noreferrer">Document uploaded ✓</a>
                  <button type="button" className="secondary" style={{ marginLeft: 8, padding: "2px 8px", fontSize: 11 }}
                          onClick={() => setUploadUrl("")}>Remove</button>
                </span>
              : <input type="file" accept="image/*,application/pdf" onChange={uploadDocument} disabled={uploading} />}
            {uploading && <small style={{ display: "block", color: "var(--muted)" }}>Uploading…</small>}
          </label>
        </div>
        <label>Remarks<input name="remarks" defaultValue="" /></label>
        <label className="checkbox-row"><input type="checkbox" name="damage_reported" /> Damage reported at the drop</label>
        <button className="primary full-button" disabled={busy || uploading}>Save ePOD</button>
      </form>}

      {selected.status === "submitted" && <div className="allocate-box">
        <p className="eyebrow">OFFICE REVIEW</p>
        <label className="reject-reason">Reason, if you are sending it back<input value={reason} onChange={event => setReason(event.target.value)} placeholder="Shortage not signed by the consignee" /></label>
      </div>}
      <div className="record-actions">
        <button className="secondary" disabled={busy || !selected.captured_at || selected.status === "rejected"} onClick={() => call(`proofs/${selected.id}/reject/`, { reason }, "ePOD sent back")}>Reject</button>
        <button className="primary" disabled={busy || !selected.captured_at || selected.status === "verified"} onClick={() => call(`proofs/${selected.id}/verify/`, {}, "ePOD verified")}>Verify</button>
      </div>

      <div className="allocate-box">
        <p className="eyebrow">PHYSICAL POD BY COURIER</p>
        {selected.courier_status === "not_sent" ? <>
          <p className="pod-note">Some consignees will only sign a physical copy. If the driver collected one, send it back here.</p>
          <form className="action-form pod-capture" onSubmit={sendByCourier}>
            <div className="form-grid">
              <label>Courier<input name="courier_name" list="courier-names" placeholder="Blue Dart" required /></label>
              <label>AWB / tracking number<input name="awb_number" required /></label>
              <label>Expected by<input name="expected_by" type="date" /></label>
              <label>Note<input name="courier_note" placeholder="Optional" /></label>
            </div>
            <datalist id="courier-names"><option value="India Post" /><option value="Blue Dart" /><option value="DTDC" /><option value="Professional Couriers" /><option value="Delhivery" /><option value="Ecom Express" /></datalist>
            <button className="primary full-button" disabled={busy}>Send by courier</button>
          </form>
        </> : <>
          <div className="tracking-grid">
            <div><span>Courier</span><strong>{selected.courier_name}</strong></div>
            <div><span>AWB</span><strong>{selected.courier_awb_number}</strong></div>
            <div><span>Dispatched</span><strong>{selected.courier_dispatched_at ? new Date(selected.courier_dispatched_at).toLocaleDateString("en-IN") : "—"}</strong></div>
            <div><span>Expected by</span><strong className={selected.courier_overdue ? "text-late" : ""}>{selected.courier_expected_by || "—"}{selected.courier_overdue ? " · overdue" : ""}</strong></div>
          </div>
          <p className={"pod-note" + (selected.courier_status === "lost" ? " warn" : "")}>
            {selected.courier_status === "delivered" ? `Received back at the office${selected.courier_received_at ? ` on ${new Date(selected.courier_received_at).toLocaleString("en-IN")}` : ""}.`
              : selected.courier_status === "lost" ? `Reported lost in transit.${selected.courier_remarks ? ` ${selected.courier_remarks}` : ""}`
              : selected.courier_status === "in_transit" ? "In transit with the courier."
              : "Dispatched, not yet marked in transit."}
          </p>
          {selected.courier_status !== "lost" && selected.courier_status !== "delivered" &&
            <label className="reject-reason">If it goes missing, note why<input value={lostReason} onChange={event => setLostReason(event.target.value)} placeholder="Misplaced at the hub" /></label>}
          {selected.courier_status !== "delivered" && <div className="record-actions">
            {selected.courier_status === "dispatched" && <button className="secondary" disabled={busy} onClick={() => call(`proofs/${selected.id}/courier-transit/`, {}, "Marked in transit")}>In transit</button>}
            {selected.courier_status !== "lost" && <button className="secondary" disabled={busy} onClick={reportLost}>Report lost</button>}
            <button className="primary" disabled={busy} onClick={() => call(`proofs/${selected.id}/courier-received/`, {}, "Physical POD received")}>Mark received</button>
          </div>}
        </>}
      </div>
    </aside></div>}
  </div>;
}

const trackingFilters: [string, string][] = [
  ["", "All"], ["dispatched", "Dispatched"], ["in_transit", "In transit"],
  ["completed", "Delivered"], ["delayed", "Running late"],
];

const isDelayed = (order: any) =>
  Boolean(order.scheduled_at) && !["completed", "cancelled"].includes(order.status) && new Date(order.scheduled_at) < new Date();

// --- Live GPS (Geotrackers) --------------------------------------------------

const GPS_STATUS_COLOR: Record<string, string> = {
  moving: "#22c55e",
  idle: "#f59e0b",
  parked: "#94a3b8",
};
// Real map tiles rather than a drawn outline: state and district boundaries,
// city, town and village names, roads and rail all come from the tile layer,
// which is what makes this readable as a map rather than as a diagram.
//
// Tiles are fetched by the operator's browser, so an air-gapped deployment or
// one behind a strict egress policy should point NEXT_PUBLIC_MAP_TILE_URL at
// an internal tile server. OpenStreetMap's public tiles carry a usage policy
// that a heavy commercial fleet should not lean on indefinitely - swapping in
// MapTiler, Mapbox or Google is a one-line change here.
const MAP_LAYERS: { id: string; label: string; url: string; attribution: string; maxZoom: number }[] = [
  {
    id: "streets", label: "Map",
    url: process.env.NEXT_PUBLIC_MAP_TILE_URL || "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  },
  {
    id: "terrain", label: "Terrain",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: 17,
  },
];
const INDIA_CENTRE: [number, number] = [22.6, 79.0];

function FleetMap({ vehicles, selectedReg, onSelect }: {
  vehicles: any[]; selectedReg: string | null; onSelect: (reg: string) => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<any>(null);
  const leaflet = useRef<any>(null);
  const cluster = useRef<any>(null);
  const markers = useRef<Record<string, any>>({});
  const selectRef = useRef(onSelect);
  const [layer, setLayer] = useState(MAP_LAYERS[0].id);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState("");

  // The click handler is rebound on every render; keeping it in a ref means the
  // marker layer does not have to be torn down and rebuilt to stay current.
  selectRef.current = onSelect;

  // Leaflet reaches for `window` at import time, and this app is statically
  // exported, so it can only be loaded once the component is actually mounted.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const L = (await import("leaflet")).default;
        // markercluster is a classic Leaflet plugin: it augments the global `L`
        // rather than exporting anything, so the global has to exist before it
        // is imported or `markerClusterGroup` is silently never defined.
        (window as unknown as { L: unknown }).L = L;
        await import("leaflet.markercluster");
        if (cancelled || !container.current || map.current) return;
        leaflet.current = L;
        // maxZoom has to be set on the map itself, not left to the tile layer:
        // the basemap is attached by a later effect, and the cluster group
        // refuses to initialise against a map whose zoom range is unknown.
        const instance = L.map(container.current, { center: INDIA_CENTRE, zoom: 5, zoomControl: true,
                                                    worldCopyJump: true, minZoom: 3, maxZoom: 19 });
        map.current = instance;

        // Clustering is a bonus, not a dependency. The plugin only attaches
        // itself when it can see a global `L`, and a bundler is free to
        // evaluate it before that global is set - so check rather than assume,
        // and fall back to plain markers instead of losing the whole map.
        const clusterable = (L as any).markerClusterGroup;
        cluster.current = typeof clusterable === "function"
          ? clusterable({
              maxClusterRadius: 46,
              showCoverageOnHover: false,
              iconCreateFunction: (group: any) => {
                const count = group.getChildCount();
                const size = count > 99 ? 44 : count > 9 ? 38 : 32;
                return L.divIcon({ html: `<div class="gps-cluster" style="width:${size}px;height:${size}px">${count}</div>`,
                                   className: "", iconSize: [size, size] });
              },
            })
          : L.layerGroup();
        cluster.current.addTo(instance);
        setReady(true);
      } catch (e) {
        // Say what actually went wrong - a silent "could not load" leaves the
        // operator with nothing to report and nobody able to fix it.
        if (!cancelled) setFailed(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; map.current?.remove(); map.current = null; };
  }, []);

  // Swap the basemap without disturbing the markers on top of it.
  useEffect(() => {
    const L = leaflet.current;
    if (!ready || !L || !map.current) return;
    const chosen = MAP_LAYERS.find(l => l.id === layer) || MAP_LAYERS[0];
    const tiles = L.tileLayer(chosen.url, { attribution: chosen.attribution, maxZoom: chosen.maxZoom });
    tiles.addTo(map.current);
    tiles.bringToBack();
    // Only drop the previous basemap once the new one has pixels, so the map
    // never flashes empty mid-swap.
    const previous = map.current.__basemap;
    const drop = () => { if (previous) map.current?.removeLayer(previous); };
    tiles.once("load", drop);
    setTimeout(drop, 2500);
    map.current.__basemap = tiles;
  }, [layer, ready]);

  useEffect(() => {
    const L = leaflet.current;
    if (!ready || !L || !cluster.current) return;

    cluster.current.clearLayers();
    markers.current = {};

    for (const vehicle of vehicles) {
      const colour = GPS_STATUS_COLOR[vehicle.gps_status] || "#94a3b8";
      const selected = vehicle.reg === selectedReg;
      const marker = L.marker([vehicle.lat, vehicle.lng], {
        icon: L.divIcon({
          className: "",
          iconSize: [16, 16],
          iconAnchor: [8, 8],
          html: `<div class="gps-pin ${vehicle.gps_status} ${selected ? "selected" : ""}"
                      style="--pin:${colour};--heading:${vehicle.heading ?? 0}"><i></i>${
                        selected ? `<b>${escapeHtml(vehicle.reg)}</b>` : ""}</div>`,
        }),
        title: `${vehicle.reg} · ${vehicle.gps_status}`,
      });
      marker.bindPopup(vehiclePopup(vehicle), { closeButton: false, minWidth: 210 });
      marker.on("click", () => selectRef.current(vehicle.reg));
      markers.current[vehicle.reg] = marker;
      cluster.current.addLayer(marker);
    }
  }, [vehicles, selectedReg, ready]);

  // Selecting a vehicle in the list should bring it into view here, including
  // when it is hidden inside a cluster.
  useEffect(() => {
    const marker = selectedReg ? markers.current[selectedReg] : null;
    if (!ready || !marker || !cluster.current) return;
    // zoomToShowLayer only exists when the cluster plugin loaded; without it a
    // plain pan to the marker does the same job.
    if (typeof cluster.current.zoomToShowLayer === "function") {
      cluster.current.zoomToShowLayer(marker, () => marker.openPopup());
    } else {
      map.current?.panTo(marker.getLatLng());
      marker.openPopup();
    }
  }, [selectedReg, ready]);

  const fitAll = () => {
    const L = leaflet.current;
    if (!L || !map.current || !vehicles.length) return;
    map.current.fitBounds(L.latLngBounds(vehicles.map(v => [v.lat, v.lng])), { padding: [40, 40] });
  };

  // The container div is never swapped out conditionally: Leaflet mutates that
  // subtree itself, and letting React replace it mid-life leaves the panes
  // reparented into whatever rendered in its place.
  return <div className="fleet-map">
    <div ref={container} style={{ height: "100%" }} />
    {failed
      ? <div className="fleet-map-overlay"><div className="data-state">The map could not be loaded.<br /><small>{failed}</small></div></div>
      : <div className="fleet-map-layers">
          {MAP_LAYERS.map(l => <button key={l.id} className={l.id === layer ? "active" : ""}
                                       onClick={() => setLayer(l.id)}>{l.label}</button>)}
          <button onClick={fitAll} title="Zoom to fit every vehicle">Fit</button>
        </div>}
  </div>;
}

// Dispatch planning route map (docs/DISPATCH-PLANNER-V2.md §6.2) - the same
// Leaflet setup as FleetMap (dynamic import, basemap switcher), drawing one
// polyline per route in a stable colour, numbered pickup/drop markers, and
// unrouted/outsourced tasks as dashed grey pickup->drop lines so the loads
// the plan could not serve are as visible as the ones it did.
function PlanMap({ routes, unroutedTasks, highlightRoute, onSelectRoute }: {
  routes: any[]; unroutedTasks: any[]; highlightRoute: number | null; onSelectRoute: (id: number | null) => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<any>(null);
  const leaflet = useRef<any>(null);
  const layerGroup = useRef<any>(null);
  const selectRef = useRef(onSelectRoute);
  const [layer, setLayer] = useState(MAP_LAYERS[0].id);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState("");
  const [showUnrouted, setShowUnrouted] = useState(true);
  const [showDeadLegs, setShowDeadLegs] = useState(true);

  selectRef.current = onSelectRoute;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const L = (await import("leaflet")).default;
        if (cancelled || !container.current || map.current) return;
        leaflet.current = L;
        const instance = L.map(container.current, { center: INDIA_CENTRE, zoom: 5, zoomControl: true,
                                                    worldCopyJump: true, minZoom: 3, maxZoom: 19 });
        map.current = instance;
        layerGroup.current = L.layerGroup().addTo(instance);
        setReady(true);
      } catch (e) {
        if (!cancelled) setFailed(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => { cancelled = true; map.current?.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const L = leaflet.current;
    if (!ready || !L || !map.current) return;
    const chosen = MAP_LAYERS.find(l => l.id === layer) || MAP_LAYERS[0];
    const tiles = L.tileLayer(chosen.url, { attribution: chosen.attribution, maxZoom: chosen.maxZoom });
    tiles.addTo(map.current);
    tiles.bringToBack();
    const previous = map.current.__basemap;
    const drop = () => { if (previous) map.current?.removeLayer(previous); };
    tiles.once("load", drop);
    setTimeout(drop, 2500);
    map.current.__basemap = tiles;
  }, [layer, ready]);

  useEffect(() => {
    const L = leaflet.current;
    if (!ready || !L || !layerGroup.current) return;
    layerGroup.current.clearLayers();
    const bounds: [number, number][] = [];

    for (const route of routes) {
      const path: [number, number][] = route.path || [];
      if (path.length < 2) continue;
      const dimmed = highlightRoute != null && highlightRoute !== route.id;
      const colour = route.colour || "#0d5f45";
      // The very first leg (vehicle start -> first stop) is the dead-km run;
      // drawn dashed and separately so it reads as different from a laden leg.
      if (showDeadLegs && path.length > 1) {
        L.polyline([path[0], path[1]], { color: colour, weight: 3, opacity: dimmed ? 0.15 : 0.6, dashArray: "2,7" }).addTo(layerGroup.current);
      }
      L.polyline(path.slice(1), { color: colour, weight: dimmed ? 2 : 4, opacity: dimmed ? 0.18 : 0.85 })
        .addTo(layerGroup.current)
        .on("click", () => selectRef.current(highlightRoute === route.id ? null : route.id));
      path.forEach(p => bounds.push(p));

      (route.stops || []).forEach((stop: any, index: number) => {
        if (stop.latitude == null || stop.longitude == null) return;
        const isPickup = stop.stop_type === "pickup";
        const marker = L.marker([Number(stop.latitude), Number(stop.longitude)], {
          icon: L.divIcon({
            className: "", iconSize: [22, 22], iconAnchor: [11, 11],
            html: `<div class="plan-stop-pin ${isPickup ? "pickup" : "drop"}" style="--pin:${colour};opacity:${dimmed ? 0.35 : 1}">${isPickup ? "▲" : index}</div>`,
          }),
        });
        marker.bindPopup(planStopPopup(stop), { closeButton: false, minWidth: 200 });
        marker.on("click", () => selectRef.current(highlightRoute === route.id ? null : route.id));
        marker.addTo(layerGroup.current);
      });
    }

    if (showUnrouted) {
      for (const task of unroutedTasks) {
        if (task.pickup_lat == null || task.dropoff_lat == null) continue;
        const from: [number, number] = [task.pickup_lat, task.pickup_lng];
        const to: [number, number] = [task.dropoff_lat, task.dropoff_lng];
        L.polyline([from, to], { color: "#8a938e", weight: 2, opacity: 0.7, dashArray: "1,6" }).addTo(layerGroup.current);
        [from, to].forEach((point, index) => {
          const marker = L.marker(point, {
            icon: L.divIcon({ className: "", iconSize: [16, 16], iconAnchor: [8, 8],
                             html: `<div class="plan-stop-pin unrouted">${index === 0 ? "▲" : "▼"}</div>` }),
          });
          marker.bindPopup(`<div class="gps-popup"><h4>${escapeHtml(task.order_number || "Unrouted")}</h4>
            <dl><dt>Status</dt><dd>${escapeHtml(task.status)}</dd>${task.reason ? `<dt>Reason</dt><dd>${escapeHtml(task.reason)}</dd>` : ""}
            ${task.outsource_estimate != null ? `<dt>Market est.</dt><dd>₹${escapeHtml(String(task.outsource_estimate))} (${escapeHtml(task.outsource_confidence || "fallback")})</dd>` : ""}</dl></div>`,
            { closeButton: false, minWidth: 190 });
          marker.addTo(layerGroup.current);
        });
        bounds.push(from, to);
      }
    }

    if (bounds.length) map.current.fitBounds(L.latLngBounds(bounds), { padding: [36, 36] });
  }, [routes, unroutedTasks, ready, highlightRoute, showUnrouted, showDeadLegs]);

  return <div className="fleet-map plan-map">
    <div ref={container} style={{ height: "100%" }} />
    {failed
      ? <div className="fleet-map-overlay"><div className="data-state">The map could not be loaded.<br /><small>{failed}</small></div></div>
      : <div className="fleet-map-layers">
          {MAP_LAYERS.map(l => <button key={l.id} className={l.id === layer ? "active" : ""} onClick={() => setLayer(l.id)}>{l.label}</button>)}
          <button className={showUnrouted ? "active" : ""} onClick={() => setShowUnrouted(!showUnrouted)}>Outsourced</button>
          <button className={showDeadLegs ? "active" : ""} onClick={() => setShowDeadLegs(!showDeadLegs)}>Dead legs</button>
        </div>}
  </div>;
}

function planStopPopup(stop: any) {
  const row = (label: string, value: string) => value ? `<dt>${label}</dt><dd>${escapeHtml(value)}</dd>` : "";
  return `<div class="gps-popup">
    <h4>${escapeHtml(stop.place_name || stop.city || "Stop")}</h4>
    <dl>
      ${row("Type", stop.stop_type === "pickup" ? "Pickup" : "Drop")}
      ${row("Order", stop.order_number)}
      ${row("Customer", stop.customer_name)}
      ${row("Load after", stop.load_after_kg != null ? `${stop.load_after_kg} kg` : "")}
      ${row("ETA", stop.planned_arrival ? new Date(stop.planned_arrival).toLocaleString() : "")}
    </dl>
  </div>`;
}

const escapeHtml = (value: string) =>
  String(value ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));

// Popup markup is handed to Leaflet as a string, so every value that came off
// the telematics feed is escaped on the way in.
function vehiclePopup(vehicle: any) {
  const row = (label: string, value: string, className = "") =>
    value ? `<dt>${label}</dt><dd class="${className}">${escapeHtml(value)}</dd>` : "";
  const temperature = vehicle.temperature_c != null
    ? row("Temp", `${vehicle.temperature_c} °C${vehicle.ac_on === false ? " (unit off)" : ""}`,
          vehicle.temperature_c > 8 ? "excursion" : "")
    : "";
  return `<div class="gps-popup">
    <h4>${escapeHtml(vehicle.reg)}</h4>
    <dl>
      ${row("Status", vehicle.state_text || vehicle.gps_status)}
      ${row("Speed", `${vehicle.speed_kph} km/h`)}
      ${temperature}
      ${row("Ignition", vehicle.ignition ? "ON" : "OFF")}
      ${row("Fleet", [vehicle.make, vehicle.model].filter(Boolean).join(" · "))}
      ${row("Last fix", vehicle.gps_time ? new Date(vehicle.gps_time).toLocaleString("en-IN") : "")}
    </dl>
    ${vehicle.address ? `<p style="margin:6px 0 0;font-size:11px">${escapeHtml(vehicle.address)}</p>` : ""}
  </div>`;
}

function LiveGpsView({ onAction }: { onAction: Notify }) {
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [diagnostic, setDiagnostic] = useState<any>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const load = async () => {
    try {
      const data = await fmsRequest<any>("live-tracking/");
      setVehicles(data.vehicles || []);
      setLastRefresh(new Date());
      setError(data.error || null);
    } catch (e) {
      setError(e instanceof Error ? e.message.slice(0, 200) : "Could not reach live tracking");
    } finally {
      setLoading(false);
    }
  };

  // The vendor endpoint is undocumented; when a field moves this is how the
  // operator gets the raw shape without needing a shell on the server.
  const diagnose = async () => {
    try { setDiagnostic(await fmsRequest<any>("live-tracking/?debug=1")); }
    catch (e) { onAction(e instanceof Error ? e.message.slice(0, 90) : "Diagnostics failed", "warn"); }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const moving = vehicles.filter(v => v.gps_status === "moving").length;
  const idle = vehicles.filter(v => v.gps_status === "idle").length;
  const parked = vehicles.filter(v => v.gps_status === "parked").length;
  const plotted = vehicles.filter(v => v.in_india);
  // A reefer above +8 °C is a cold-chain excursion, not a rounding error.
  const excursions = vehicles.filter(v => v.temperature_c != null && v.temperature_c > 8);
  const noFix = vehicles.filter(v => !v.lat || !v.lng);
  // Reporting a position, but not one anywhere near India - worth showing as a
  // number rather than dropping the pin on the floor and saying nothing.
  const offMap = vehicles.filter(v => v.lat && v.lng && !v.in_india);
  const sel = vehicles.find(v => v.reg === selected) || null;

  // 110 vehicles is too many to scan by eye; let the operator narrow by plate,
  // by where the truck is, or by what it is doing.
  const needle = query.trim().toLowerCase();
  const shown = vehicles.filter(v =>
    (!statusFilter || v.gps_status === statusFilter) &&
    (!needle || `${v.reg} ${v.label} ${v.address}`.toLowerCase().includes(needle)));

  const toggle = (reg: string) => setSelected(prev => (prev === reg ? null : reg));

  return <div className="module-page">
    <div className="module-title">
      <div><p className="eyebrow">LIVE GPS TRACKING</p><h2>Fleet positions</h2>
        <p>Real-time positions from Geotrackers · auto-refreshes every 30 s.{lastRefresh ? ` Last: ${lastRefresh.toLocaleTimeString("en-IN")}` : ""}</p></div>
      <div className="toolbar-actions">
        <button className="chip" onClick={diagnose}>⚙ Diagnose feed</button>
        <button className="primary module-action" onClick={load}>↻ Refresh</button>
      </div>
    </div>
    {error && <div className="pod-note warn" style={{ marginBottom: 12 }}>Geotrackers: {error}</div>}
    {diagnostic && <div className="pod-note" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>Raw feed diagnostics</strong>
        <button className="chip" onClick={() => setDiagnostic(null)}>Dismiss</button>
      </div>
      <p style={{ margin: "6px 0" }}>
        Payload <code>{diagnostic.payload_type}</code> · {diagnostic.rows_detected} row(s) detected
        {diagnostic.top_level_keys ? ` · keys: ${diagnostic.top_level_keys.join(", ")}` : ""}
      </p>
      <pre style={{ maxHeight: 220, overflow: "auto", fontSize: 11, background: "var(--surface-2,#f1f5f9)", padding: 10, borderRadius: 6 }}>
        {JSON.stringify(diagnostic.first_row_raw ?? diagnostic.raw_excerpt ?? diagnostic.error, null, 2)}
      </pre>
    </div>}
    <div className="module-stats">
      <div className="module-stat"><span>Reporting</span><strong>{loading ? "—" : vehicles.length}</strong><small>{plotted.length} plotted on map</small></div>
      <div className="module-stat"><span>Moving</span><strong>{loading ? "—" : moving}</strong><small>Speed &gt; 3 km/h</small></div>
      <div className="module-stat warn"><span>Idle</span><strong>{loading ? "—" : idle}</strong><small>Engine on, stationary</small></div>
      <div className="module-stat"><span>Parked</span><strong>{loading ? "—" : parked}</strong><small>Engine off</small></div>
      {excursions.length > 0 && <div className="module-stat warn"><span>Temp. excursions</span><strong>{excursions.length}</strong><small>Reefer above +8 °C</small></div>}
    </div>
    {!loading && !error && vehicles.length > 0 && plotted.length === 0 &&
      <div className="pod-note warn" style={{ marginBottom: 12 }}>
        {vehicles.length} vehicle(s) are reporting but none have a usable position
        ({noFix.length} with no GPS fix, {offMap.length} outside India). Use <strong>Diagnose feed</strong> to see the raw fields.
      </div>}
    <div className="tracking-page-layout">
      <section className="module-table-card shipment-list">
        <div className="module-toolbar"><div><strong>Vehicles</strong><span>{shown.length} of {vehicles.length}</span></div></div>
        <div style={{ padding: "0 12px 8px" }}>
          <input value={query} onChange={e => setQuery(e.target.value)} aria-label="Search vehicles"
                 placeholder="Search plate or location…" style={{ width: "100%" }} />
        </div>
        <div className="toolbar-actions wrap">
          {[["", "All"], ["moving", "Moving"], ["idle", "Idle"], ["parked", "Parked"]].map(([value, label]) =>
            <button key={value} className={value === statusFilter ? "chip active" : "chip"}
                    onClick={() => setStatusFilter(value)}>{label}</button>)}
        </div>
        <div className="shipment-rows">
          {shown.map(v => <div key={v.reg} className={"shipment-row" + (v.reg === selected ? " active" : "")} onClick={() => toggle(v.reg)}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 8, minWidth: 0 }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: GPS_STATUS_COLOR[v.gps_status] || "#94a3b8", flexShrink: 0, marginTop: 4 }} />
              <div style={{ minWidth: 0 }}>
                <strong>{v.reg}</strong>
                <small style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {v.address || v.label || "No location"}
                </small>
              </div>
            </div>
            <div className="shipment-row-meta">
              <span className={"status " + (v.gps_status === "moving" ? "completed" : v.gps_status === "idle" ? "assigned" : "available")}>{v.gps_status}</span>
              {!v.in_india && <span className="status cancelled">no fix</span>}
              {/* Reefer body temperature is the number this fleet is actually watching. */}
              {v.temperature_c != null && <small style={{ color: v.temperature_c > 8 ? "#dc2626" : "#0284c7", fontWeight: 600 }}>{v.temperature_c}°C</small>}
              {v.speed_kph > 0 && <small>{v.speed_kph} km/h</small>}
            </div>
          </div>)}
          {!loading && !shown.length && <div className="data-state">
            {error ? "Geotrackers is unreachable." : vehicles.length ? "No vehicles match this filter." : "No vehicles reporting."}
          </div>}
        </div>
      </section>

      <section className="track-detail" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "12px 16px 6px", borderBottom: "1px solid var(--border)" }}>
          <strong>Live fleet map</strong>
          <small style={{ marginLeft: 10, color: "var(--text-2)" }}>
            <span style={{ color: "#22c55e" }}>●</span> moving &nbsp;<span style={{ color: "#f59e0b" }}>●</span> idle &nbsp;<span style={{ color: "#94a3b8" }}>●</span> parked &nbsp;· click a pin for detail
          </small>
        </div>
        <FleetMap vehicles={plotted} selectedReg={selected} onSelect={toggle} />
        {(noFix.length > 0 || offMap.length > 0) && <p style={{ padding: "6px 16px", margin: 0, fontSize: 12, color: "var(--text-2,#64748b)" }}>
          Not shown: {noFix.length} without a GPS fix{offMap.length ? `, ${offMap.length} reporting a position outside India` : ""}.
        </p>}
        {sel
          ? <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border)" }}>
              <div className="tracking-grid">
                <div><span>Registration</span><strong>{sel.reg}</strong></div>
                <div><span>Status</span><strong style={{ color: GPS_STATUS_COLOR[sel.gps_status] || "#94a3b8" }}>{sel.state_text || sel.gps_status}</strong></div>
                <div><span>Speed</span><strong>{sel.speed_kph} km/h</strong></div>
                <div><span>Ignition</span><strong>{sel.ignition ? "ON" : "OFF"}</strong></div>
                {sel.temperature_c != null && <div>
                  <span>Body temperature</span>
                  <strong style={{ color: sel.temperature_c > 8 ? "#dc2626" : "#0284c7" }}>
                    {sel.temperature_c}°C{sel.ac_on === false ? " · unit off" : ""}
                  </strong></div>}
                {sel.battery_v != null && <div><span>Battery</span><strong>{sel.battery_v} V</strong></div>}
                {(sel.make || sel.model) && <div><span>Body / model</span><strong>{[sel.make, sel.model].filter(Boolean).join(" · ")}</strong></div>}
                <div><span>FMS status</span><strong>{sel.fms_status ? String(sel.fms_status).replaceAll("_", " ") : "Not in FMS"}</strong></div>
                <div><span>Last fix</span><strong>{sel.gps_time ? new Date(sel.gps_time).toLocaleString("en-IN") : "—"}</strong></div>
              </div>
              {sel.label && <p className="pod-note" style={{ marginTop: 8 }}>{sel.label}</p>}
              {sel.lat && sel.lng && <p className="pod-note" style={{ marginTop: 8 }}>
                📍 {sel.address || `${Number(sel.lat).toFixed(5)}, ${Number(sel.lng).toFixed(5)}`}
                {sel.coords_swapped ? " · latitude and longitude arrived transposed and were corrected" : ""}
              </p>}
              {!sel.lat && <p className="pod-note warn" style={{ marginTop: 8 }}>This device is not reporting a GPS fix.</p>}
              {sel.lat && sel.lng && <a href={`https://maps.google.com/?q=${sel.lat},${sel.lng}`} target="_blank" rel="noreferrer" className="chip" style={{ display: "inline-block", marginTop: 8 }}>Open in Google Maps ↗</a>}
            </div>
          : <div className="data-state" style={{ padding: 16 }}>Click a vehicle to see its details.</div>}
      </section>
    </div>
  </div>;
}

// --- Shipment tracking -------------------------------------------------------

function TrackingView({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [tab, setTab] = useState<"live" | "shipments">("live");
  const [orders, setOrders] = useState<any[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const [zone, setZone] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setLoading(true);
    fmsRequest<any>(wholeSet("orders/")).then(payload => {
      const records = asList(payload).filter((order: any) => order.status !== "cancelled");
      setOrders(records);
      setSelectedId((current: number | null) => (current && records.some((order: any) => order.id === current)) ? current
        : (records.find((order: any) => ["dispatched", "in_transit"].includes(order.status)) || records[0])?.id ?? null);
    }).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  const selected = orders.find(order => order.id === selectedId) || null;
  const position = selected?.last_position;

  // The zone a coordinate falls inside is a live lookup, not something the order carries.
  useEffect(() => {
    setZone(null);
    if (!position?.latitude || !position?.longitude) return;
    fmsRequest<any>(`zones/locate/?lat=${position.latitude}&lng=${position.longitude}`)
      .then(payload => setZone(payload.zones?.[0] || null)).catch(() => undefined);
  }, [selected?.id, position?.recorded_at]);

  const shown = orders.filter(order => (filter === "delayed" ? isDelayed(order) : filter ? order.status === filter : true));
  const active = orders.filter(order => ["dispatched", "in_transit"].includes(order.status));
  const delayed = orders.filter(isDelayed);
  const today = new Date().toISOString().slice(0, 10);
  const deliveredToday = orders.filter(order => order.completed_at && String(order.completed_at).slice(0, 10) === today);
  const avgProgress = active.length
    ? Math.round(active.reduce((sum, order) => sum + Number(order.progress_percent || 0), 0) / active.length) : 0;

  const logCheckpoint = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selected) return;
    const form = event.currentTarget as HTMLFormElement;
    const data = new FormData(form);
    setBusy(true);
    try {
      await fmsRequest(`orders/${selected.id}/activity/`, { method: "POST", body: JSON.stringify({
        code: "GPS_PING", city: String(data.get("city") || ""), details: String(data.get("details") || ""),
        latitude: data.get("latitude") || undefined, longitude: data.get("longitude") || undefined }) });
      onAction("Checkpoint logged");
      form.reset();
      load();
    } catch (e) {
      onAction(e instanceof Error ? e.message.slice(0, 100) : "Could not log the checkpoint", "warn");
    } finally { setBusy(false); }
  };

  const useDeviceLocation = (form: HTMLFormElement | null) => {
    if (!form) return;
    if (!navigator.geolocation) { onAction("This browser will not share its location", "warn"); return; }
    navigator.geolocation.getCurrentPosition(
      pos => {
        (form.elements.namedItem("latitude") as HTMLInputElement).value = pos.coords.latitude.toFixed(6);
        (form.elements.namedItem("longitude") as HTMLInputElement).value = pos.coords.longitude.toFixed(6);
      },
      () => onAction("Could not read this device's location", "warn"));
  };

  const copyLink = async (order: any) => {
    const link = `${window.location.origin}/track?number=${order.tracking_number}`;
    try { await navigator.clipboard.writeText(link); onAction("Tracking link copied"); }
    catch { onAction(link, "warn"); }
  };

  // A straight-line estimate at a typical highway running speed - not a real ETA feed, but a
  // useful number for a lane where no telematics is wired in yet.
  const remainingKm = selected ? Number(selected.distance_km || 0) * (1 - Number(selected.progress_percent || 0) / 100) : 0;
  const eta = selected && ["dispatched", "in_transit"].includes(selected.status)
    ? new Date(Date.now() + (remainingKm / 40) * 3600 * 1000) : null;
  const pinFraction = Math.min(1, Number(selected?.progress_percent || 0) / 100);

  const tabBar = <div style={{ display: "flex", gap: 8, padding: "4px 24px 0" }}>
    <button className={tab === "live" ? "chip active" : "chip"} onClick={() => setTab("live")}>⌖ Live GPS</button>
    <button className={tab === "shipments" ? "chip active" : "chip"} onClick={() => setTab("shipments")}>◈ Shipments</button>
  </div>;

  if (tab === "live") return <>{tabBar}<LiveGpsView onAction={onAction} /></>;

  return <div className="module-page">
    {tabBar}
    <div className="module-title"><div><p className="eyebrow">SHIPMENT TRACKING</p><h2>Track fleet operations</h2><p>Where every consignment is right now, its movement history, and a link to share with the consignee.</p></div><button className="primary module-action" onClick={load}>↻ Refresh</button></div>
    <div className="module-stats">
      <div className="module-stat"><span>On the road</span><strong>{loading ? "—" : active.length}</strong><small>Dispatched or in transit</small></div>
      <div className="module-stat warn"><span>Running late</span><strong>{loading ? "—" : delayed.length}</strong><small>Past their scheduled time</small></div>
      <div className="module-stat"><span>Delivered today</span><strong>{loading ? "—" : deliveredToday.length}</strong><small>Completed since midnight</small></div>
      <div className="module-stat"><span>Average progress</span><strong>{loading ? "—" : `${avgProgress}%`}</strong><small>Across active shipments</small></div>
    </div>

    <div className="tracking-page-layout">
      <section className="module-table-card shipment-list">
        <div className="module-toolbar"><div><strong>Shipments</strong><span>{shown.length} of {orders.length}</span></div></div>
        <div className="toolbar-actions wrap">{trackingFilters.map(([value, label]) => <button key={value} className={value === filter ? "chip active" : "chip"} onClick={() => setFilter(value)}>{label}</button>)}</div>
        <div className="shipment-rows">
          {shown.map(order => <div key={order.id} className={"shipment-row" + (order.id === selectedId ? " active" : "")} onClick={() => setSelectedId(order.id)}>
            <div><strong>{order.number}</strong><small>{order.pickup_city} → {order.dropoff_city}</small></div>
            <div className="shipment-row-meta">
              <span className={"status " + String(order.status).replaceAll("_", "-")}>{String(order.status).replaceAll("_", " ")}</span>
              {isDelayed(order) && <span className="status cancelled">delayed</span>}
            </div>
            <div className="shipment-progress"><i style={{ width: `${order.progress_percent || 0}%` }} /></div>
          </div>)}
          {!loading && !shown.length && <div className="data-state">No shipments match this filter.</div>}
        </div>
      </section>

      {selected ? <section className="track-detail">
        <div className="module-toolbar"><div><strong>{selected.number}</strong><span>{selected.tracking_number}</span></div>
          <div className="toolbar-actions"><span className={"status " + String(selected.status).replaceAll("_", "-")}>{String(selected.status).replaceAll("_", " ")}</span><button className="chip" onClick={() => copyLink(selected)}>⧉ Copy tracking link</button></div></div>

        <div className="operations-map compact-map">
          <div className="map-road r1" />
          <span className="city start-city">{selected.pickup_city}</span>
          <span className="city finish-city">{selected.dropoff_city}</span>
          <span className="map-pin start">●</span>
          <span className="map-pin finish">●</span>
          <div className="map-progress" style={{ width: `${Math.max(4, Math.min(96, selected.progress_percent || 0))}%` }} />
          <span className="map-pin vehicle" style={{ left: `${17 + (83 - 17) * pinFraction}%`, top: `${60 + (42 - 60) * pinFraction}%` }}>▰</span>
        </div>

        <div className="tracking-grid">
          <div><span>Customer</span><strong>{selected.customer_name}</strong></div>
          <div><span>Vehicle · driver</span><strong>{selected.vehicle_number || "—"} · {selected.driver_name || "—"}</strong></div>
          <div><span>Distance</span><strong>{Number(selected.distance_km).toFixed(0)} km · {selected.progress_percent || 0}% covered</strong></div>
          <div><span>{eta ? "Est. arrival" : "Scheduled"}</span><strong className={isDelayed(selected) ? "text-late" : ""}>{eta ? eta.toLocaleString("en-IN") : selected.scheduled_at ? new Date(selected.scheduled_at).toLocaleString("en-IN") : "—"}</strong></div>
          <div><span>Created</span><strong>{stamp(selected.created_at)}</strong></div>
          <div><span>Last updated</span><strong>{stamp(selected.updated_at)}</strong></div>
        </div>
        {isDelayed(selected) && <p className="pod-note warn">Running behind its scheduled time of {new Date(selected.scheduled_at).toLocaleString("en-IN")}.</p>}
        {position && <p className="pod-note">Last seen near <strong>{position.city || "an unnamed point"}</strong>{zone ? `, inside the ${zone.name} zone` : ""} · {new Date(position.recorded_at).toLocaleString("en-IN")}</p>}

        <div className="record-timeline"><p className="eyebrow">MOVEMENT HISTORY</p>
          {(selected.activities || []).slice(0, 8).map((activity: any) => <div key={activity.id}><i /><span><strong>{String(activity.code).replaceAll("_", " ")}</strong><small>{activity.details || activity.status}{activity.city ? ` · ${activity.city}` : ""}</small></span><time>{new Date(activity.recorded_at).toLocaleString("en-IN")}</time></div>)}
          {!(selected.activities || []).length && <div><i /><span><strong>No activity yet</strong></span><time>—</time></div>}
        </div>

        {!["completed", "cancelled"].includes(selected.status) && <form className="action-form pod-capture" onSubmit={logCheckpoint}>
          <p className="eyebrow">LOG A CHECKPOINT</p>
          <div className="form-grid">
            <label>City / landmark<input name="city" placeholder="Vadodara toll plaza" /></label>
            <label>Note<input name="details" placeholder="Crossed toll, on schedule" /></label>
            <label>Latitude<input name="latitude" type="number" step="any" /></label>
            <label>Longitude<input name="longitude" type="number" step="any" /></label>
          </div>
          <button type="button" className="chip" onClick={event => useDeviceLocation((event.currentTarget as HTMLElement).closest("form"))}>Use device location</button>
          <button className="primary full-button" disabled={busy}>Log checkpoint</button>
        </form>}
      </section> : <section className="track-detail"><div className="data-state">{loading ? "Loading shipments…" : "No shipments to track yet."}</div></section>}
    </div>
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
               ["Indent type", detail.indent_type === "part_load" ? "Part load" : "Full truck load (FTL)"],
               ["Delivery access", detail.delivery_access === "no_entry_area" ? "No-entry area" : "Without no-entry restriction"],
               ["Temperature required", detail.temp_min_c != null && detail.temp_max_c != null ? `${detail.temp_min_c} to ${detail.temp_max_c} °C ±${detail.temp_tolerance_c} °C` : detail.temperature_class],
               ["Expected freight", rupees(detail.expected_rate)],
               ["Required by", detail.required_at ? new Date(detail.required_at).toLocaleString("en-IN") : ""],
               ["Expected delivery", detail.expected_delivery_at ? new Date(detail.expected_delivery_at).toLocaleString("en-IN") : ""],
               ["Expected running", detail.expected_running_km ? `${Number(detail.expected_running_km).toLocaleString("en-IN")} km` : ""],
               ["Allocated truck", detail.vehicle_number], ["Driver", detail.driver_name],
               ["Order", detail.order_number], ["Remarks", detail.remarks],
               ["Created", stamp(detail.created_at)], ["Last updated", stamp(detail.updated_at)]]}
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

// ---------------------------------------------------------------------------
// Report views
// ---------------------------------------------------------------------------

function downloadCSV(filename: string, headers: string[], rows: (string | number | null | undefined)[][]) {
  const escape = (v: string | number | null | undefined) => {
    const s = String(v ?? "");
    return s.includes(",") || s.includes('"') || s.includes("\n") ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers, ...rows].map(row => row.map(escape).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + lines], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function ReportDateRange({ range, setRange, onApply }: { range: { from: string; to: string }; setRange: (r: { from: string; to: string }) => void; onApply: () => void }) {
  return (
    <div className="report-range">
      <label>From<input type="date" value={range.from} onChange={e => setRange({ ...range, from: e.target.value })} /></label>
      <label>To<input type="date" value={range.to} onChange={e => setRange({ ...range, to: e.target.value })} /></label>
      <button className="chip" onClick={onApply}>Apply</button>
    </div>
  );
}

function DriverAvailabilityReport({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");

  const load = () => {
    setLoading(true); setData(null);
    fmsRequest<any>("reports/driver-availability/").then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  const drivers: any[] = data?.drivers || [];
  const visible = drivers.filter(d =>
    (d.name + d.phone + d.licence_number + d.status + d.home_city).toLowerCase().includes(query.toLowerCase())
  );

  const statusBadge = (s: string) => <span className={"status " + s.toLowerCase().replaceAll(" ", "-").replaceAll("_", "-")}>{s.replaceAll("_", " ")}</span>;
  const expiryBadge = (s: string) => {
    const cls = s === "expired" ? "expired" : s === "expiring_soon" ? "pending" : s === "valid" ? "active" : "idle";
    return <span className={"status " + cls}>{s.replaceAll("_", " ")}</span>;
  };

  const handleDownload = () => {
    const headers = ["Driver", "Phone", "Licence Number", "Licence Expiry", "Expiry Status", "Status", "Home City", "Active Trips"];
    const rows = visible.map((d: any) => [d.name, d.phone, d.licence_number, d.licence_expiry || "", d.licence_expiry_status.replaceAll("_", " "), d.status, d.home_city || "", d.active_trips]);
    downloadCSV("driver-availability.csv", headers, rows);
    onAction("Driver availability report downloaded");
  };

  return (
    <div className="module-page">
      <div className="module-title">
        <div><p className="eyebrow">REPORTS</p><h2>Driver &amp; Availability</h2><p>Licence status, current availability and trip count for every driver in the fleet.</p></div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="secondary module-action" onClick={handleDownload} disabled={!data || loading}>⇩ Download CSV</button>
          <button className="primary module-action" onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {data && (
        <div className="module-stats">
          <div className="module-stat"><span>Total drivers</span><strong>{data.summary.total}</strong><small>Registered</small></div>
          <div className="module-stat"><span>Available</span><strong>{data.summary.available}</strong><small>Ready to dispatch</small></div>
          <div className="module-stat"><span>On trip</span><strong>{data.summary.on_trip}</strong><small>Currently running</small></div>
          <div className="module-stat"><span>Licence issues</span><strong>{data.summary.licences_expired + data.summary.licences_expiring_soon}</strong><small>{data.summary.licences_expired} expired · {data.summary.licences_expiring_soon} expiring</small></div>
        </div>
      )}
      <section className="module-table-card">
        <div className="module-toolbar">
          <div><strong>All drivers</strong><span>{loading ? "Loading…" : `${visible.length} drivers`}</span></div>
          <div className="toolbar-actions">
            <input placeholder="Search drivers…" value={query} onChange={e => setQuery(e.target.value)} />
            <button onClick={load}>↻ Refresh</button>
            <button onClick={handleDownload} disabled={!data || loading}>⇩ Export</button>
          </div>
        </div>
        {loading ? <div className="data-state">Loading report…</div> : !data ? <div className="data-state error">Could not load the report.</div> : visible.length === 0 ? <div className="data-state">No drivers found.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Driver</th><th>Phone</th><th>Licence</th><th>Licence expiry</th><th>Expiry status</th><th>Status</th><th>Home city</th><th>Active trips</th></tr></thead>
              <tbody>
                {visible.map((d: any) => (
                  <tr key={d.id}>
                    <td><strong>{d.name}</strong></td>
                    <td>{d.phone}</td>
                    <td>{d.licence_number}</td>
                    <td>{d.licence_expiry || "—"}</td>
                    <td>{expiryBadge(d.licence_expiry_status)}</td>
                    <td>{statusBadge(d.status)}</td>
                    <td>{d.home_city || "—"}</td>
                    <td>{d.active_trips}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function FleetReport({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");

  const load = () => {
    setLoading(true); setData(null);
    fmsRequest<any>("reports/fleet/").then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  const vehicles: any[] = data?.vehicles || [];
  const visible = vehicles.filter(v =>
    (v.registration_number + v.vehicle_type + v.status + v.ownership + v.current_place).toLowerCase().includes(query.toLowerCase())
  );

  const statusBadge = (s: string) => <span className={"status " + s.toLowerCase().replaceAll("_", "-")}>{s.replaceAll("_", " ")}</span>;
  const complianceBadge = (s: string) => {
    const cls = s === "expired" ? "expired" : s === "expiring_soon" ? "pending" : s === "valid" ? "active" : "idle";
    return <span className={"status " + cls}>{s.replaceAll("_", " ")}</span>;
  };

  const handleDownload = () => {
    const headers = ["Registration", "Type", "Ownership", "Capacity (kg)", "Status", "Insurance Expiry", "Insurance Status", "Permit Expiry", "Permit Status", "Odometer (km)", "Total Trips"];
    const rows = visible.map((v: any) => [v.registration_number, v.vehicle_type, v.ownership, v.capacity_kg, v.status, v.insurance_expiry || "", v.insurance_status.replaceAll("_", " "), v.permit_expiry || "", v.permit_status.replaceAll("_", " "), v.current_odometer_km, v.trips_total]);
    downloadCSV("fleet-report.csv", headers, rows);
    onAction("Fleet report downloaded");
  };

  return (
    <div className="module-page">
      <div className="module-title">
        <div><p className="eyebrow">REPORTS</p><h2>Fleet Report</h2><p>Vehicle-by-vehicle status, utilisation, and insurance &amp; permit compliance across the entire fleet.</p></div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="secondary module-action" onClick={handleDownload} disabled={!data || loading}>⇩ Download CSV</button>
          <button className="primary module-action" onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {data && (
        <div className="module-stats">
          <div className="module-stat"><span>Total vehicles</span><strong>{data.summary.total}</strong><small>In fleet master</small></div>
          <div className="module-stat"><span>Available</span><strong>{data.summary.available}</strong><small>Ready to allocate</small></div>
          <div className="module-stat"><span>On trip</span><strong>{data.summary.on_trip}</strong><small>Currently moving</small></div>
          <div className="module-stat"><span>Compliance issues</span><strong>{data.summary.insurance_expired + data.summary.permit_expired}</strong><small>{data.summary.insurance_expired} ins · {data.summary.permit_expired} permit expired</small></div>
        </div>
      )}
      <section className="module-table-card">
        <div className="module-toolbar">
          <div><strong>All vehicles</strong><span>{loading ? "Loading…" : `${visible.length} vehicles`}</span></div>
          <div className="toolbar-actions">
            <input placeholder="Search vehicles…" value={query} onChange={e => setQuery(e.target.value)} />
            <button onClick={load}>↻ Refresh</button>
            <button onClick={handleDownload} disabled={!data || loading}>⇩ Export</button>
          </div>
        </div>
        {loading ? <div className="data-state">Loading report…</div> : !data ? <div className="data-state error">Could not load the report.</div> : visible.length === 0 ? <div className="data-state">No vehicles found.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Registration</th><th>Type</th><th>Ownership</th><th>Capacity (kg)</th><th>Status</th><th>Insurance</th><th>Permit</th><th>Odometer (km)</th><th>Total trips</th></tr></thead>
              <tbody>
                {visible.map((v: any) => (
                  <tr key={v.id}>
                    <td><strong>{v.registration_number}</strong></td>
                    <td>{v.vehicle_type}</td>
                    <td>{v.ownership}</td>
                    <td>{Number(v.capacity_kg).toLocaleString("en-IN")}</td>
                    <td>{statusBadge(v.status)}</td>
                    <td>{complianceBadge(v.insurance_status)}<small> {v.insurance_expiry || "—"}</small></td>
                    <td>{complianceBadge(v.permit_status)}<small> {v.permit_expiry || "—"}</small></td>
                    <td>{Number(v.current_odometer_km).toLocaleString("en-IN")}</td>
                    <td>{v.trips_total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function CustomerInvoicesReport({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState({ from: "", to: "" });
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState(0);

  const load = () => {
    setLoading(true); setData(null);
    const q = range.from || range.to ? `?from=${range.from}&to=${range.to}` : "";
    fmsRequest<any>("reports/customer-invoices/" + q).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  const invoices: any[] = data?.invoices || [];
  const byCustomer: any[] = data?.by_customer || [];
  const visibleInvoices = invoices.filter(i =>
    (i.number + i.customer + i.status).toLowerCase().includes(query.toLowerCase())
  );
  const visibleCustomers = byCustomer.filter(c => c.customer.toLowerCase().includes(query.toLowerCase()));

  const statusBadge = (s: string) => <span className={"status " + (s === "paid" ? "active" : s === "overdue" ? "expired" : "pending")}>{s}</span>;

  const handleDownload = () => {
    if (tab === 0) {
      const headers = ["Invoice", "Customer", "GSTIN", "Freight", "GST", "Total", "Due Date", "Days Overdue", "Status", "Date"];
      const rows = visibleInvoices.map((i: any) => [i.number, i.customer, i.customer_gstin, i.freight_amount, i.tax_amount, i.total_amount, i.due_date, i.days_overdue, i.status, i.created_at]);
      downloadCSV("customer-invoices.csv", headers, rows);
    } else {
      const headers = ["Customer", "Invoice Count", "Total Billed", "Paid", "Outstanding"];
      const rows = visibleCustomers.map((c: any) => [c.customer, c.count, c.total, c.paid, c.outstanding]);
      downloadCSV("customer-invoices-by-customer.csv", headers, rows);
    }
    onAction("Customer invoice report downloaded");
  };

  return (
    <div className="module-page">
      <div className="module-title">
        <div><p className="eyebrow">REPORTS</p><h2>Customer Invoice Report</h2><p>Invoice-level detail and customer-wise outstanding with ageing.</p></div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="secondary module-action" onClick={handleDownload} disabled={!data || loading}>⇩ Download CSV</button>
          <button className="primary module-action" onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {data && (
        <div className="module-stats">
          <div className="module-stat"><span>Total invoiced</span><strong>{rupees(data.summary.total_invoiced)}</strong><small>{data.summary.invoice_count} invoices</small></div>
          <div className="module-stat"><span>Collected</span><strong>{rupees(data.summary.total_paid)}</strong><small>Paid invoices</small></div>
          <div className="module-stat"><span>Outstanding</span><strong>{rupees(data.summary.total_outstanding)}</strong><small>Pending collection</small></div>
          <div className="module-stat"><span>Overdue</span><strong>{data.summary.overdue_count}</strong><small>Past due date</small></div>
        </div>
      )}
      <ReportDateRange range={range} setRange={setRange} onApply={load} />
      <div className="report-tabs">
        <button className={tab === 0 ? "chip active" : "chip"} onClick={() => setTab(0)}>Invoice detail</button>
        <button className={tab === 1 ? "chip active" : "chip"} onClick={() => setTab(1)}>By customer</button>
      </div>
      <section className="module-table-card">
        <div className="module-toolbar">
          <div><strong>{tab === 0 ? "All invoices" : "Customer summary"}</strong><span>{loading ? "Loading…" : tab === 0 ? `${visibleInvoices.length} invoices` : `${visibleCustomers.length} customers`}</span></div>
          <div className="toolbar-actions"><input placeholder="Search…" value={query} onChange={e => setQuery(e.target.value)} /><button onClick={handleDownload} disabled={!data || loading}>⇩ Export</button></div>
        </div>
        {loading ? <div className="data-state">Loading report…</div> : !data ? <div className="data-state error">Could not load the report.</div> : tab === 0 ? (
          visibleInvoices.length === 0 ? <div className="data-state">No invoices in this period.</div> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Invoice</th><th>Customer</th><th>Freight</th><th>GST</th><th>Total</th><th>Due date</th><th>Days overdue</th><th>Status</th></tr></thead>
                <tbody>
                  {visibleInvoices.map((i: any) => (
                    <tr key={i.id}>
                      <td><strong>{i.number}</strong></td>
                      <td>{i.customer}</td>
                      <td>{rupees(i.freight_amount)}</td>
                      <td>{rupees(i.tax_amount)}</td>
                      <td><strong>{rupees(i.total_amount)}</strong></td>
                      <td>{i.due_date}</td>
                      <td>{i.days_overdue > 0 ? <span className="status expired">{i.days_overdue}d</span> : "—"}</td>
                      <td>{statusBadge(i.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          visibleCustomers.length === 0 ? <div className="data-state">No customer data.</div> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Customer</th><th>Invoices</th><th>Total billed</th><th>Paid</th><th>Outstanding</th></tr></thead>
                <tbody>
                  {visibleCustomers.map((c: any) => (
                    <tr key={c.customer}>
                      <td><strong>{c.customer}</strong></td>
                      <td>{c.count}</td>
                      <td>{rupees(c.total)}</td>
                      <td>{rupees(c.paid)}</td>
                      <td><strong>{rupees(c.outstanding)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </section>
    </div>
  );
}

function VehicleSettlementReport({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState({ from: "", to: "" });
  const [query, setQuery] = useState("");

  const load = () => {
    setLoading(true); setData(null);
    const q = range.from || range.to ? `?from=${range.from}&to=${range.to}` : "";
    fmsRequest<any>("reports/vehicle-settlement/" + q).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  const settlements: any[] = data?.settlements || [];
  const visible = settlements.filter(s =>
    (s.driver + s.trip + s.vehicle + s.route + s.status).toLowerCase().includes(query.toLowerCase())
  );

  const statusBadge = (s: string) => <span className={"status " + (s === "settled" ? "active" : "pending")}>{s}</span>;

  const handleDownload = () => {
    const headers = ["Driver", "Phone", "Trip", "Vehicle", "Route", "Advance (INR)", "Approved Expenses (INR)", "Net Payable (INR)", "Status", "Trip Status", "Date"];
    const rows = visible.map((s: any) => [s.driver, s.driver_phone, s.trip, s.vehicle, s.route, s.advance_amount, s.approved_expenses, s.net_payable, s.status, s.trip_status, s.created_at]);
    downloadCSV("vehicle-settlement.csv", headers, rows);
    onAction("Vehicle settlement report downloaded");
  };

  return (
    <div className="module-page">
      <div className="module-title">
        <div><p className="eyebrow">REPORTS</p><h2>Vehicle Settlement Report</h2><p>Trip-wise driver advance, approved expenses and net payable for every settlement.</p></div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="secondary module-action" onClick={handleDownload} disabled={!data || loading}>⇩ Download CSV</button>
          <button className="primary module-action" onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {data && (
        <div className="module-stats">
          <div className="module-stat"><span>Total advance paid</span><strong>{rupees(data.summary.total_advance)}</strong><small>{data.summary.settlement_count} settlements</small></div>
          <div className="module-stat"><span>Total expenses</span><strong>{rupees(data.summary.total_approved_expenses)}</strong><small>Approved on-road costs</small></div>
          <div className="module-stat"><span>Net payable</span><strong>{rupees(data.summary.total_net_payable)}</strong><small>Advance difference</small></div>
          <div className="module-stat"><span>Pending</span><strong>{data.summary.pending_count}</strong><small>{data.summary.settled_count} settled</small></div>
        </div>
      )}
      <ReportDateRange range={range} setRange={setRange} onApply={load} />
      <section className="module-table-card">
        <div className="module-toolbar">
          <div><strong>All settlements</strong><span>{loading ? "Loading…" : `${visible.length} records`}</span></div>
          <div className="toolbar-actions"><input placeholder="Search…" value={query} onChange={e => setQuery(e.target.value)} /><button onClick={load}>↻ Refresh</button><button onClick={handleDownload} disabled={!data || loading}>⇩ Export</button></div>
        </div>
        {loading ? <div className="data-state">Loading report…</div> : !data ? <div className="data-state error">Could not load the report.</div> : visible.length === 0 ? <div className="data-state">No settlements in this period.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Driver</th><th>Trip</th><th>Vehicle</th><th>Route</th><th>Advance</th><th>Expenses</th><th>Net payable</th><th>Status</th></tr></thead>
              <tbody>
                {visible.map((s: any) => (
                  <tr key={s.id}>
                    <td><strong>{s.driver}</strong><small>{s.driver_phone}</small></td>
                    <td>{s.trip}</td>
                    <td>{s.vehicle}</td>
                    <td>{s.route}</td>
                    <td>{rupees(s.advance_amount)}</td>
                    <td>{rupees(s.approved_expenses)}</td>
                    <td><strong className={s.net_payable < 0 ? "text-green" : ""}>{rupees(Math.abs(s.net_payable))}{s.net_payable < 0 ? " recover" : " pay"}</strong></td>
                    <td>{statusBadge(s.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function SalesReport({ reloadKey, onAction }: { reloadKey: number; onAction: Notify }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState({ from: "", to: "" });
  const [query, setQuery] = useState("");

  const load = () => {
    setLoading(true); setData(null);
    const q = range.from || range.to ? `?from=${range.from}&to=${range.to}` : "";
    fmsRequest<any>("reports/sales/" + q).then(setData).catch(() => setData(null)).finally(() => setLoading(false));
  };
  useEffect(load, [reloadKey]);

  const quotes: any[] = data?.quotes || [];
  const visible = quotes.filter(q =>
    (q.number + q.customer + q.lane + q.status).toLowerCase().includes(query.toLowerCase())
  );

  const statusBadge = (s: string) => {
    const cls = s === "accepted" || s === "won" ? "active" : s === "draft" ? "idle" : s === "expired" || s === "lost" ? "expired" : "pending";
    return <span className={"status " + cls}>{s}</span>;
  };

  const handleDownload = () => {
    const headers = ["Quote Number", "Customer", "GSTIN", "Origin", "Destination", "Freight (INR)", "Valid Until", "Status", "Date"];
    const rows = visible.map((q: any) => [q.number, q.customer, q.customer_gstin, q.origin, q.destination, q.freight_amount, q.valid_until, q.status, q.created_at]);
    downloadCSV("sales-report.csv", headers, rows);
    onAction("Sales report downloaded");
  };

  return (
    <div className="module-page">
      <div className="module-title">
        <div><p className="eyebrow">REPORTS</p><h2>Sales Report</h2><p>Quotation pipeline, conversion rate and freight value by customer and lane.</p></div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="secondary module-action" onClick={handleDownload} disabled={!data || loading}>⇩ Download CSV</button>
          <button className="primary module-action" onClick={load}>↻ Refresh</button>
        </div>
      </div>
      {data && (
        <div className="module-stats">
          <div className="module-stat"><span>Total quotes</span><strong>{data.summary.total_quotes}</strong><small>In selected period</small></div>
          <div className="module-stat"><span>Pipeline value</span><strong>{rupees(data.summary.total_value)}</strong><small>All quotes</small></div>
          <div className="module-stat"><span>Won value</span><strong>{rupees(data.summary.won_value)}</strong><small>Accepted / won</small></div>
          <div className="module-stat"><span>Conversion</span><strong>{data.summary.conversion_rate}%</strong><small>Won ÷ total</small></div>
        </div>
      )}
      {data?.summary?.by_status && (
        <div className="module-stats" style={{ marginTop: 0 }}>
          {(data.summary.by_status as any[]).map((s: any) => (
            <div className="module-stat" key={s.status}>
              <span>{s.status}</span><strong>{rupees(s.value)}</strong><small>{s.count} quotes</small>
            </div>
          ))}
        </div>
      )}
      <ReportDateRange range={range} setRange={setRange} onApply={load} />
      <section className="module-table-card">
        <div className="module-toolbar">
          <div><strong>All quotations</strong><span>{loading ? "Loading…" : `${visible.length} quotes`}</span></div>
          <div className="toolbar-actions"><input placeholder="Search…" value={query} onChange={e => setQuery(e.target.value)} /><button onClick={load}>↻ Refresh</button><button onClick={handleDownload} disabled={!data || loading}>⇩ Export</button></div>
        </div>
        {loading ? <div className="data-state">Loading report…</div> : !data ? <div className="data-state error">Could not load the report.</div> : visible.length === 0 ? <div className="data-state">No quotations in this period.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Quote</th><th>Customer</th><th>Lane</th><th>Freight</th><th>Valid until</th><th>Status</th><th>Date</th></tr></thead>
              <tbody>
                {visible.map((q: any) => (
                  <tr key={q.id}>
                    <td><strong>{q.number}</strong></td>
                    <td>{q.customer}</td>
                    <td>{q.lane}</td>
                    <td><strong>{rupees(q.freight_amount)}</strong></td>
                    <td>{q.valid_until}</td>
                    <td>{statusBadge(q.status)}</td>
                    <td>{q.created_at}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
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

        {active === "Overview" ? <div className="page-grid"><section className="quick-actions"><div><p className="eyebrow">QUICK ACTIONS</p><h2>Run daily fleet operations</h2></div><button onClick={() => setAction("lr")}><span>▤</span><b>Generate LR</b><small>Book consignment</small></button><button onClick={() => setAction("trip")}><span>▦</span><b>Create trip sheet</b><small>Allocate vehicle & driver</small></button><button onClick={() => setAction("invoice")}><span>₹</span><b>Generate invoice</b><small>Bill a delivered consignment</small></button><button onClick={() => setActive("Tracking")}><span>⌖</span><b>Track vehicles</b><small>View live GPS map</small></button><button onClick={() => setAction("order")}><span>◈</span><b>Book order</b><small>FleetOps consignment</small></button><button onClick={() => setAction("fuel")}><span>⛽</span><b>Log diesel</b><small>Fuel & mileage entry</small></button></section>
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

        </div> : active === "Modules" ? <FeatureHub onAction={show} /> : active === "Planning" ? <DispatchPlanningView onAction={show} /> : active === "Scenario Profiles" ? <ScenarioProfilesView reloadKey={dataVersion} onAction={show} /> : active === "Orders" ? <OrdersView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Hires" ? <HiresView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Fleet" ? <FleetVehiclesView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Rates" ? <RatesView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Compliance" ? <ComplianceView reloadKey={dataVersion} openAction={setAction} /> : active === "ePOD" ? <EpodView reloadKey={dataVersion} onAction={show} /> : active === "Tracking" ? <TrackingView reloadKey={dataVersion} onAction={show} /> : active === "Fleets" ? <FleetsView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Indents" ? <IndentsView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Users" ? <UsersView reloadKey={dataVersion} onAction={show} openAction={setAction} /> : active === "Roles" ? <RolesView reloadKey={dataVersion} onAction={show} /> : active === "Vouchers" ? <VouchersView reloadKey={dataVersion} onAction={show} /> : active === "Payments" ? <PaymentsView reloadKey={dataVersion} onAction={show} /> : active === "Financials" ? <FinancialsView reloadKey={dataVersion} onAction={show} /> : active === "Driver & Availability" ? <DriverAvailabilityReport reloadKey={dataVersion} onAction={show} /> : active === "Fleet Report" ? <FleetReport reloadKey={dataVersion} onAction={show} /> : active === "Customer Invoices" ? <CustomerInvoicesReport reloadKey={dataVersion} onAction={show} /> : active === "Vehicle Settlement" ? <VehicleSettlementReport reloadKey={dataVersion} onAction={show} /> : active === "Sales Report" ? <SalesReport reloadKey={dataVersion} onAction={show} /> : fleetOpsPages.includes(active) ? <FleetOpsView name={active} reloadKey={dataVersion} onAction={show} openAction={setAction} /> : <ModuleView name={active as keyof typeof modules} reloadKey={dataVersion} onAction={show} openAction={setAction} />}
      </section>
      {action && <ActionPanel type={action} onClose={() => setAction("")} onCreated={() => setDataVersion(v => v + 1)} />}
      {toast && <div className={"toast " + toast.tone}>{toast.tone === "warn" ? "⚠" : "✓"} {toast.text}</div>}
    </main>
  );
}

