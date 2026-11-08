import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { connectionsApi } from "../api/connections";
import { PrivacyNote, ProviderCards, StatusChip, useConnections } from "./ConnectionsPanel";
import type { ProviderConn } from "../types/connections";
import "./connections.css";

const DISMISS_KEY = "studio.setup.dismissed"; // non-secret flag only

function wasDismissed(): boolean {
  try { return localStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; }
}
export function rememberDismissed(): void {
  try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* ignore */ }
}

/** Fetches /api/connections; firstRun is true only if the backend says so and the user has not skipped it. */
export function useFirstRun() {
  const [firstRun, setFirstRun] = useState(false);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      const s = await connectionsApi.get();
      setFirstRun(s.first_run && !wasDismissed());
    } catch {
      setFirstRun(false); // 404 / offline: never block the app
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return { firstRun, loading, refresh };
}

interface Props { open: boolean; onClose: () => void; onConnected?: () => void }

export function SetupWizard({ open, onClose, onConnected }: Props) {
  const [step, setStep] = useState(0);
  const conn = useConnections();
  const { replaceProvider, refresh } = conn;
  const dialogRef = useRef<HTMLDivElement>(null);

  const close = useCallback(() => {
    rememberDismissed();
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    setStep(0);
    void refresh();
    const prev = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => prev?.focus?.();
  }, [open, refresh]);

  useEffect(() => { if (open) dialogRef.current?.focus(); }, [step, open]);

  if (!open) return null;

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === "Escape") { e.stopPropagation(); close(); return; }
    if (e.key !== "Tab" || !dialogRef.current) return;
    const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]):not([readonly])'));
    if (items.length === 0) return;
    const first = items[0], last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialogRef.current)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
  }

  const providers: ProviderConn[] = conn.state?.providers ?? [];
  const connected = providers.filter((p) => p.status === "connected");
  const update = (p: ProviderConn) => { replaceProvider(p); onConnected?.(); };

  return (
    <div className="wz-backdrop">
      <div ref={dialogRef} className="wz-dialog" role="dialog" aria-modal="true" aria-labelledby="wz-title" tabIndex={-1} onKeyDown={onKeyDown}>
        <p className="wz-step">Step {step + 1} of 3</p>

        {step === 0 && (
          <>
            <h2 id="wz-title">Welcome to AI Collection Studio</h2>
            <p>Create a whole set of matching game-card images from one plan, review them, and export the ones you like.</p>
            <p>To make real images you connect an image provider. Or try the demo first, no key needed.</p>
            <div className="wz-actions">
              <button type="button" className="primary cn-btn" onClick={() => setStep(1)}>Connect a provider</button>
              <button type="button" className="ghost" onClick={close}>Try the demo without a key</button>
            </div>
            <p className="hint">The demo uses the free Mock provider, which makes simple placeholder images.</p>
          </>
        )}

        {step === 1 && (
          <>
            <h2 id="wz-title">Connect your providers</h2>
            <p className="hint">You can connect one, both, or skip and do it later in Settings.</p>
            <PrivacyNote />
            <ProviderCards conn={{ ...conn, replaceProvider: update }} />
            <div className="wz-actions">
              <button type="button" className="ghost" onClick={() => setStep(0)}>Back</button>
              <button type="button" className="primary cn-btn" onClick={() => setStep(2)}>
                {connected.length > 0 ? "Continue" : "Skip for now"}
              </button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h2 id="wz-title">{connected.length > 0 ? "You're all set" : "Using the demo for now"}</h2>
            <ul className="wz-summary" aria-live="polite">
              {providers.map((p) => (
                <li key={p.id}><strong>{p.label}</strong> <StatusChip status={p.status} /></li>
              ))}
              {providers.length === 0 && <li>No providers to show.</li>}
            </ul>
            {connected.length === 0 && <p className="hint">You can connect a provider any time in Settings.</p>}
            <div className="wz-actions">
              <button type="button" className="ghost" onClick={() => setStep(1)}>Back</button>
              <button type="button" className="primary cn-btn" onClick={close}>Start creating</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
