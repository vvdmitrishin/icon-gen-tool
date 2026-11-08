import { useEffect, useRef, useState } from "react";
import { GOLD_TIP, MAX_GOLD_COPY } from "../lib/cards";
import { InfoTip } from "./CategoryDistribution";

interface Props {
  value: number;
  max: number;
  onChange: (v: number) => void;
  /** Grand Set: the value is fixed to the number of cards */
  disabled?: boolean;
}

const SPARKS = 7;

/** Golden-cards slider: gold track with a gentle shimmer; while dragging the thumb glows and throws a few sparks (CSS only). */
export function GoldSlider({ value, max, onChange, disabled = false }: Props) {
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
        <InfoTip id="tip-gold-slider" tip={{ title: "★ Golden cards", text: GOLD_TIP }} align="left" />
        <output className="gold-val" htmlFor="gold-range" aria-live="polite">{value}</output>
      </div>
      <div className="gold-wrap" style={{ ["--gp" as string]: pct }}>
        <input
          id="gold-range" type="range" min={0} max={Math.max(0, max)} step={1} value={value} disabled={disabled}
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
      <p className="hint">{disabled ? "Grand Set: every card is golden, so this equals the number of cards." : MAX_GOLD_COPY}</p>
    </div>
  );
}
