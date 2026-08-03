import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Phloz Fleet — Transport ERP",
  description: "A unified fleet operations and finance workspace for Indian transporters.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
