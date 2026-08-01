import { useEffect, useRef, useState } from "react";
import { api, type KitAsset, type VerifyResult } from "../lib/api";
import { shortHash } from "../lib/format";
import "./evidence.css";

/** The provenance drawer — every claim, clickable. */
export function EvidenceDrawer({
  epId,
  asset,
  onClose,
}: {
  epId: string;
  asset: KitAsset;
  onClose: () => void;
}) {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [state, setState] = useState<"idle" | "fetching" | "done" | "error">("idle");
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const runVerify = async () => {
    setState("fetching");
    try {
      setResult(await api.verify(epId, asset.id));
      setState("done");
    } catch {
      setState("error");
    }
  };

  return (
    <div className="drawer-scrim" onClick={onClose} role="presentation">
      <aside
        ref={panelRef}
        className="drawer card"
        role="dialog"
        aria-label="Provenance and verification"
        aria-modal="true"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drawer__head">
          <h2 className="display drawer__title">Provenance</h2>
          <button className="drawer__close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <dl className="prov">
          <Row label="Asset">{asset.label}</Row>
          <Row label="Provider · model">
            {asset.provider} · {asset.model}
          </Row>
          <Row label="Genblaze run">
            <code className="num">{asset.run_id}</code>
          </Row>
          {asset.parent_run_id && (
            <Row label="Parent run (lineage)">
              <code className="num">{asset.parent_run_id}</code>
              <span className="prov__hint">generation {asset.generation} — regenerated from human feedback</span>
            </Row>
          )}
          <Row label="B2 object key">
            <code className="prov__key num">{asset.b2_key}</code>
          </Row>
          <Row label="Recorded SHA-256">
            <code className="num">{shortHash(asset.sha256, 20)}</code>
          </Row>
          {asset.manifest_key && (
            <Row label="Manifest">
              <code className="prov__key num">{asset.manifest_key}</code>
            </Row>
          )}
        </dl>

        <div className="verify">
          <button
            className="btn btn--signal verify__btn"
            onClick={runVerify}
            disabled={state === "fetching"}
          >
            {state === "fetching" ? "Fetching bytes from B2…" : "Fetch bytes & re-hash now"}
          </button>
          <p className="verify__what caps">
            downloads the object from Backblaze B2 this second and recomputes its hash
          </p>

          {state === "done" && result && (
            <div className={`verdict ${result.match ? "verdict--ok" : "verdict--bad"} rise`} role="status">
              <span className="verdict__badge">{result.match ? "✓ BYTES MATCH" : "✗ MISMATCH"}</span>
              <dl className="verdict__detail num">
                <div>
                  <dt>expected</dt>
                  <dd>{shortHash(result.expected_sha256, 24)}</dd>
                </div>
                <div>
                  <dt>fetched</dt>
                  <dd>{shortHash(result.fetched_sha256, 24)}</dd>
                </div>
                <div>
                  <dt>size</dt>
                  <dd>{result.size_bytes.toLocaleString()} bytes</dd>
                </div>
                <div>
                  <dt>round trip</dt>
                  <dd>{result.fetch_ms} ms</dd>
                </div>
                {result.manifest_canonical_hash && (
                  <div>
                    <dt>manifest hash</dt>
                    <dd>{shortHash(result.manifest_canonical_hash, 24)}</dd>
                  </div>
                )}
              </dl>
            </div>
          )}
          {state === "error" && (
            <p className="verify__err">Verification request failed — the API said no. Try again.</p>
          )}
        </div>
      </aside>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="prov__row">
      <dt className="caps">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
