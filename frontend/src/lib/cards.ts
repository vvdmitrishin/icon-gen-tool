/** Shared helpers for card-set sizes, variant labels and category presets. */

/** The backend keeps variant ids 'A'..'E'; the UI counts 1, 2, 3... (matches the Variants dropdown). */
export function vlabel(id: string | null | undefined): string {
  if (!id) return "";
  const c = id.trim().toUpperCase().charCodeAt(0);
  return c >= 65 && c <= 90 ? String(c - 64) : id;
}

export const DEFAULT_CARDS = 10;
export const DEFAULT_SIZE = 1024;
export const MAX_GOLD_COPY =
  "Golden cards are the most valuable cards: key characters of the collection in an ironic, emotional situation. Max 2 characters per card.";

export const OUTPUT_SIZES = [24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024] as const;

export type Cat = "1" | "2" | "3" | "4";
export type DistCounts = Record<Cat, number>;
export const CATS: Cat[] = ["1", "2", "3", "4"];

const WEIGHTS = [2, 2, 3, 3];

/** Client-side mirror of the backend's balanced split (proportions 2:2:3:3). The backend stays the source of truth. */
export function balancedSplit(total: number): DistCounts {
  const t = Math.max(0, Math.floor(total));
  let arr: number[];
  if (t < 4) {
    arr = [0, 0, 0, 0];
    for (let i = 0; i < t; i++) arr[i] = 1;
  } else {
    const exact = WEIGHTS.map((w) => (t * w) / 10);
    arr = exact.map((x) => Math.max(1, Math.floor(x)));
    let sum = arr.reduce((a, b) => a + b, 0);
    while (sum > t) {
      let k = 0;
      arr.forEach((v, i) => { if (v > arr[k]) k = i; });
      arr[k] -= 1; sum -= 1;
    }
    while (sum < t) {
      let k = 0, best = -1;
      exact.forEach((x, i) => { const r = x - arr[i]; if (r >= best) { best = r; k = i; } });
      arr[k] += 1; sum += 1;
    }
  }
  return { "1": arr[0], "2": arr[1], "3": arr[2], "4": arr[3] };
}

/** Adapts custom counts to a new standard-card total (keeps the user's shape where possible). */
export function fitCounts(counts: DistCounts, standard: number): DistCounts {
  const out = { ...counts };
  const min = standard >= 4 ? 1 : 0;
  const sum = () => CATS.reduce((s, c) => s + out[c], 0);
  let guard = 100;
  while (sum() > standard && guard-- > 0) {
    const k = CATS.reduce((a, c) => (out[c] > out[a] ? c : a), "1" as Cat);
    if (out[k] <= min) break;
    out[k] -= 1;
  }
  while (sum() < standard && guard-- > 0) {
    const k = CATS.reduce((a, c) => (out[c] < out[a] ? c : a), "1" as Cat);
    out[k] += 1;
  }
  return out;
}

export const catText = (c: number) => (c === 5 ? "★" : String(c));
