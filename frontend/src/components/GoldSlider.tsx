import { useEffect, useRef, useState } from "react";
import { MAX_GOLD_COPY } from "../lib/cards";

interface Props {
  value: number;
  max: number;
  onChange: (v: number) => void;
}

const SPARKS = 7;

/** Golden-cards slider: gold track with a gentle shimmer; while dragging the thumb glows and throws a few sparks (CSS only). */
export function GoldSlider({ value, max, onChange }: Props) {
  const [drag, setDrag] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const pct = max > 0 ? value / max : 0;

  // keyboard changes also flash the effect briefly
  const flash = () => {
    setDrag(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setDrag(false), 700);
  };

  return (
    <div className={`gold ${drag ? "drag" : ""}`}>
      <div className="gold-head">
        <label className="lbl" htmlFor="gold-range"><span className="gold-star" aria-hidden>★</span> Golden cards</label>
        <output className="gold-val" htmlFor="gold-range" aria-live="polite">{value}</output>
      </div>
      <div className="gold-wrap" style={{ ["--gp" as string]: pct }}>
        <input
          id="gold-range" type="range" min={0} max={Math.max(0, max)} step={1} value={value}
          aria-valuetext={`${value} golden card${value === 1 ? "" : "s"}`}
          onChange={(e) => { onChange(Number(e.target.value)); flash(); }}
          onPointerDown={() => { window.clearTimeout(timer.current); setDrag(true); }}
          onPointerUp={() => setDrag(false)}
          onPointerCancel={() => setDrag(false)}
          onBlur={() => setDrag(false)}
        />
        <span className="gold-sparks" aria-hidden>
          {Array.from({ length: SPARKS }, (_, i) => <i key={i} style={{ ["--i" as string]: i }} />)}
        </span>
      </div>
      <p className="hint">{MAX_GOLD_COPY}</p>
    </div>
  );
}
