import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAIR Voucher Portal",
  description: "Design, approve, generate and issue MAIR discount vouchers.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
