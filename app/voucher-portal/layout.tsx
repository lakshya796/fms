import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "MAIR Voucher Portal",
  description: "Design voucher cards, generate barcoded batches, and track them through approval.",
};

/* The brand palette itself lives at :root, so this class is not what makes the
 * portal green. What it does is mark the route as a voucher surface for the few
 * rules that repaint styles borrowed from the fleet console - chiefly the
 * sign-in card, which is not a .voucher-page and so is reachable no other way.
 * Wrapping here rather than on each page also covers error.tsx. */
export default function VoucherPortalLayout({ children }: { children: React.ReactNode }) {
  return <div className="mair">{children}</div>;
}
