import { redirect } from "next/navigation";

// The portal is the only thing this app serves, so / goes straight to it
// rather than showing a landing page nobody asked for.
export default function Home() {
  redirect("/voucher-portal");
}
