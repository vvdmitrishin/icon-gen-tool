import { useState } from "react";
import { api, ApiError } from "../api/client";

/** The browser pane does not always finish `<a download>` files, so the local server can also copy the ZIP into the user's Downloads folder. */
export function SaveExport({ exportId }: { exportId: string }) {
  const [state, setState] = useState<{ busy: boolean; path?: string; err?: string }>({ busy: false });
  async function save() {
    setState({ busy: true });
    try {
      setState({ busy: false, path: (await api.saveExport(exportId)).path });
    } catch (e) {
      setState({ busy: false, err: e instanceof ApiError ? e.message : "Could not save the file" });
    }
  }
  return (
    <span className="saveexp">
      <button type="button" className="ghost sm" disabled={state.busy} onClick={save} title="Copies the ZIP into your Downloads folder">
        {state.busy ? "Saving…" : "Save to Downloads"}
      </button>
      {state.path && <span className="chip ok tiny" title={state.path}>Saved: {state.path}</span>}
      {state.err && <span className="chip warn tiny">{state.err}</span>}
    </span>
  );
}
