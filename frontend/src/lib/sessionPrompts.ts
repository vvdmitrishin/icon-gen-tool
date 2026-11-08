import { useCallback, useEffect, useState } from "react";

/**
 * Session-only prompt overrides ("Apply to Session"): temporary experiments with the System Style, the Category 1-5 rules and
 * the Closing Constraints. Stored ONLY in this browser tab's sessionStorage, so they survive a page refresh and are gone in a new
 * tab/session. The permanent Prompt Library is never touched. The overrides are sent with every generation request; the server
 * resolves them once per job into an immutable snapshot (so a running job never changes if the Producer keeps editing).
 */
export const SESSION_EDITABLE = ["system_style", "category_1", "category_2", "category_3", "category_4", "category_5", "image_closing"] as const;
export type SessionTemplate = (typeof SESSION_EDITABLE)[number];

const KEY = "ics.promptOverrides.v1";
const EVENT = "ics-prompt-overrides-changed";
let memory: Record<string, string> = {}; // fallback when sessionStorage is unavailable

export function readSessionOverrides(): Record<string, string> {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return memory;
    const v = JSON.parse(raw);
    if (v && typeof v === "object") {
      const out: Record<string, string> = {};
      for (const n of SESSION_EDITABLE) if (typeof v[n] === "string" && v[n].trim()) out[n] = v[n];
      return out;
    }
  } catch { /* private mode / blocked */ }
  return memory;
}

function writeOverrides(v: Record<string, string>) {
  memory = v;
  try { window.sessionStorage.setItem(KEY, JSON.stringify(v)); } catch { /* memory fallback only */ }
  window.dispatchEvent(new Event(EVENT));
}

export interface SessionPrompts {
  overrides: Record<string, string>;
  names: string[];
  count: number;
  has: (name: string) => boolean;
  apply: (name: string, text: string) => void;
  reset: (name: string) => void;
  resetAll: () => void;
}

export function useSessionPrompts(): SessionPrompts {
  const [overrides, setOverrides] = useState<Record<string, string>>(readSessionOverrides);
  useEffect(() => {
    const sync = () => setOverrides(readSessionOverrides());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  const apply = useCallback((name: string, text: string) => writeOverrides({ ...readSessionOverrides(), [name]: text.trim() }), []);
  const reset = useCallback((name: string) => {
    const cur = { ...readSessionOverrides() };
    delete cur[name];
    writeOverrides(cur);
  }, []);
  const resetAll = useCallback(() => writeOverrides({}), []);
  const names = Object.keys(overrides);
  return { overrides, names, count: names.length, has: (n) => n in overrides, apply, reset, resetAll };
}
