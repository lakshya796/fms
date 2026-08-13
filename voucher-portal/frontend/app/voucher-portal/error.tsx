"use client";

// Route-level boundary for /voucher-portal. Without one, a render error in any
// screen unmounts the whole page and leaves an operator staring at a blank
// browser window with no way back. This keeps the failure on-screen, named,
// and recoverable.

export default function VoucherPortalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <main className="voucher-page">
    <section className="voucher-card" style={{ maxWidth: 620, margin: "80px auto" }}>
      <div className="voucher-card-head"><h2>This screen hit an error</h2></div>
      <p style={{ fontSize: 12, color: "var(--voucher-muted)", lineHeight: 1.7 }}>
        Nothing you were working on has been saved. Try again — if it keeps happening, send this message to
        whoever maintains the portal:
      </p>
      <div className="form-error" style={{ marginTop: 10, whiteSpace: "pre-wrap" }}>
        {error.message || "Unknown error"}{error.digest ? ` (${error.digest})` : ""}
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
        <button type="button" className="primary" style={{ width: "auto", padding: "0 18px", height: 36 }} onClick={reset}>
          Try again
        </button>
        <button type="button" className="secondary" style={{ width: "auto", padding: "0 18px", height: 36 }}
                onClick={() => window.location.reload()}>
          Reload the page
        </button>
      </div>
    </section>
  </main>;
}
