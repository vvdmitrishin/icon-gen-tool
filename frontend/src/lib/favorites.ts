import { useCallback, useEffect, useState } from "react";

/**
 * "My Selection": the Producer's session shortlist of generated images.
 *  - stored in sessionStorage (this browser tab only): survives page refreshes, starts empty in a new session;
 *  - holds only stable references (artifact / job / card ids) plus a small snapshot for display, never image data;
 *  - separated per Collection + Set + Set type, so unrelated sets are never mixed and a set's selection is restored
 *    when the Producer switches back to it;
 *  - one entry = one exact generated image (artifact_id), so a newer generation never replaces a starred one.
 */
export interface FavItem {
  artifact_id: string;
  job_id: string;
  card_id: string;
  variant_id: string;
  version: number | null;
  object_name: string;
  category: number;
  position: number;
  provider: string;
  model: string;
  batch: number | null;
  image_url: string;
  added_at: number;
}

const KEY = "ics.favorites.v1";
const EVENT = "ics-favorites-changed";
let memory: Record<string, FavItem[]> | null = null; // fallback when sessionStorage is unavailable

/** Same normalisation as the backend's theme_key(): Collection + Set + Set type. */
export function themeKey(collection: string, set: string, setType: string = "standard"): string {
  const n = (t: string) => (t ?? "").trim().toLowerCase().replace(/\s+/g, " ");
  return `${n(collection)}|${n(set)}|${setType === "grand" ? "grand" : "standard"}`;
}

function readAll(): Record<string, FavItem[]> {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (raw) {
      const v = JSON.parse(raw);
      if (v && typeof v === "object") return v as Record<string, FavItem[]>;
    }
    return memory ?? {};
  } catch { return memory ?? {}; }
}

function writeAll(v: Record<string, FavItem[]>) {
  memory = v;
  try { window.sessionStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private mode / blocked: memory fallback only */ }
  window.dispatchEvent(new Event(EVENT));
}

export interface Favorites {
  items: FavItem[];
  has: (artifactId: string) => boolean;
  toggle: (item: FavItem) => void;
  remove: (artifactId: string) => void;
  clear: () => void;
}

export function useFavorites(key: string): Favorites {
  const [all, setAll] = useState<Record<string, FavItem[]>>(readAll);
  useEffect(() => {
    const sync = () => setAll(readAll());
    window.addEventListener(EVENT, sync);
    return () => window.removeEventListener(EVENT, sync);
  }, []);
  const items = all[key] ?? [];
  const mutate = useCallback((fn: (cur: FavItem[]) => FavItem[]) => {
    const cur = readAll();
    writeAll({ ...cur, [key]: fn(cur[key] ?? []) });
  }, [key]);
  return {
    items,
    has: (id) => items.some((i) => i.artifact_id === id),
    toggle: (item) => mutate((cur) => (cur.some((i) => i.artifact_id === item.artifact_id)
      ? cur.filter((i) => i.artifact_id !== item.artifact_id) : [...cur, item])),
    remove: (id) => mutate((cur) => cur.filter((i) => i.artifact_id !== id)),
    clear: () => mutate(() => []),
  };
}

/** Local mirror of the export report, so the Producer sees the compliance picture before exporting (the server is authoritative). */
export function selectionSummary(items: FavItem[], setType: "standard" | "grand") {
  const counts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  items.forEach((i) => { counts[i.category] = (counts[i.category] ?? 0) + 1; });
  const std = counts[1] + counts[2] + counts[3] + counts[4];
  const covered = [1, 2, 3, 4].every((c) => counts[c] >= 1);
  const warnings: string[] = [];
  let tenCard: boolean | null = null;
  if (setType === "grand") {
    if (items.some((i) => i.category !== 5)) warnings.push("A Grand Set should contain only golden (★) cards.");
  } else {
    tenCard = items.length === 10 && counts[5] === 0 && covered;
    if (items.length !== 10) warnings.push(`${items.length} image${items.length === 1 ? "" : "s"} selected; a standard Set has exactly 10 cards.`);
    if (counts[5]) warnings.push(`${counts[5]} golden card(s) are not part of a standard Set.`);
    if (std && !covered) warnings.push("Not all four standard categories (Cat. 1–4) are covered.");
  }
  return { counts, tenCard, warnings };
}
