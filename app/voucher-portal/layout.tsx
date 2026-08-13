import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "MAIR Voucher Portal",
  description: "Design voucher cards, generate barcoded batches, and track them through approval.",
};

/* The .mair class is what re-themes this route. globals.css keeps the brand
 * colours in custom properties and overrides them under .mair, so the portal is
 * MAIR green while /vouchers - which shares every one of these class names -
 * stays on its own ADCOOP palette. Wrapping here rather than on each page also
 * covers error.tsx, which renders the same shell. */
export default function VoucherPortalLayout({ children }: { children: React.ReactNode }) {
  return <div className="mair">{children}</div>;
}
