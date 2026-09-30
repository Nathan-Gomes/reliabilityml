import { useMemo, useState, type ReactNode } from "react";

/* Small SVG chart kit. Thin marks, recessive grid, a hover readout on every chart, and text in ink
   colours (never the series colour). */

type Pt = { x: number; y: number };

function scale(domain: [number, number], range: [number, number]) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return (v: number) => r0 + (v - d0) * k;
}

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

export interface LineSeries {
  name: string;
  values: (number | null)[];
  className: string; // CSS class that sets the stroke colour
}

export function LineChart({
  ts,
  series,
  height = 160,
  yMax,
  yFormat = (v) => v.toFixed(2),
  thresholds = [],
  bands = [],
  logY = false,
  label,
  width = 640,
}: {
  ts: string[];
  series: LineSeries[];
  height?: number;
  yMax?: number;
  yFormat?: (v: number) => string;
  thresholds?: { value: number; label: string }[];
  bands?: { from: string; to: string; label: string }[];
  logY?: boolean;
  label: string;
  width?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const pad = { l: 44, r: 12, t: 10, b: 22 };
  const times = useMemo(() => ts.map((t) => new Date(t).getTime()), [ts]);
  const tx = (v: number) => (logY ? Math.log10(Math.max(v, 0.01)) : v);
  const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null && Number.isFinite(v)));
  const top = yMax ?? niceMax(Math.max(...all, ...thresholds.map((t) => t.value), 0) * 1.08);
  const y0 = logY ? tx(0.01) : 0;
  const x = scale([times[0], times[times.length - 1]], [pad.l, width - pad.r]);
  const y = scale([y0, tx(top)], [height - pad.b, pad.t]);
  const ticks = logY ? [0.1, 1, 10, 100].filter((t) => t <= top) : [0, top / 2, top];
  const path = (values: (number | null)[]) => {
    let d = "";
    values.forEach((v, i) => {
      if (v === null || !Number.isFinite(v)) return;
      const cmd = d === "" || values[i - 1] === null ? "M" : "L";
      d += `${cmd}${x(times[i]).toFixed(1)},${y(tx(Math.min(v, top))).toFixed(1)}`;
    });
    return d;
  };
  const dayTicks = useMemo(() => {
    const out: number[] = [];
    const span = times[times.length - 1] - times[0];
    const step = span > 3 * 86400000 ? 86400000 * Math.ceil(span / 86400000 / 7) : span > 86400000 ? 21600000 : 3600000 * 2;
    const first = Math.ceil(times[0] / step) * step;
    for (let t = first; t <= times[times.length - 1]; t += step) out.push(t);
    return out;
  }, [times]);
  const tickLabel = (t: number) => {
    const d = new Date(t);
    const span = times[times.length - 1] - times[0];
    return span > 86400000 * 2
      ? d.toLocaleDateString("en-CA", { month: "short", day: "numeric" })
      : d.toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });
  };

  function onMove(e: React.MouseEvent<SVGRectElement>) {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * (width - pad.l - pad.r) + pad.l;
    const t = times[0] + ((px - pad.l) / (width - pad.l - pad.r)) * (times[times.length - 1] - times[0]);
    let best = 0;
    for (let i = 1; i < times.length; i++) if (Math.abs(times[i] - t) < Math.abs(times[best] - t)) best = i;
    setHover(best);
  }

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label}>
        {bands.map((b) => {
          const a = x(Math.max(new Date(b.from).getTime(), times[0]));
          const z = x(Math.min(new Date(b.to).getTime(), times[times.length - 1]));
          if (z <= a) return null;
          return (
            <g key={b.from}>
              <rect x={a} y={pad.t} width={Math.max(2, z - a)} height={height - pad.t - pad.b} className="band" />
              <text x={Math.min(a + 3, width - pad.r - 80)} y={pad.t + 10} className="band-label">
                {b.label}
              </text>
            </g>
          );
        })}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(tx(t))} y2={y(tx(t))} className="grid" />
            <text x={pad.l - 6} y={y(tx(t)) + 3} className="axis" textAnchor="end">
              {yFormat(t)}
            </text>
          </g>
        ))}
        {dayTicks.map((t) => (
          <text key={t} x={x(t)} y={height - 6} className="axis" textAnchor="middle">
            {tickLabel(t)}
          </text>
        ))}
        {thresholds.map((th) => (
          <g key={th.label}>
            <line x1={pad.l} x2={width - pad.r} y1={y(tx(th.value))} y2={y(tx(th.value))} className="threshold" />
            <text x={width - pad.r} y={y(tx(th.value)) - 4} className="axis" textAnchor="end">
              {th.label}
            </text>
          </g>
        ))}
        {series.map((s) => (
          <path key={s.name} d={path(s.values)} className={`line ${s.className}`} />
        ))}
        {hover !== null && (
          <line x1={x(times[hover])} x2={x(times[hover])} y1={pad.t} y2={height - pad.b} className="crosshair" />
        )}
        <rect
          x={pad.l}
          y={pad.t}
          width={width - pad.l - pad.r}
          height={height - pad.t - pad.b}
          fill="transparent"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        />
      </svg>
      {hover !== null && (
        <figcaption className="readout">
          <strong>{new Date(times[hover]).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}</strong>
          {series.map((s) => (
            <span key={s.name}>
              <i className={`key ${s.className}`} /> {s.name}: {s.values[hover] === null ? "–" : yFormat(s.values[hover] as number)}
            </span>
          ))}
        </figcaption>
      )}
    </figure>
  );
}

export function CurvePlot({
  groups,
  xLabel,
  yLabel,
  marker,
}: {
  groups: { name: string; className: string; points: Pt[]; tips: string[] }[];
  xLabel: string;
  yLabel: string;
  marker?: { x: number; y: number; label: string };
}) {
  const [tip, setTip] = useState<string | null>(null);
  const width = 560;
  const height = 300;
  const pad = { l: 48, r: 14, t: 12, b: 38 };
  const xs = groups.flatMap((g) => g.points.map((p) => p.x));
  const xMax = niceMax(Math.max(...xs, 1));
  const x = scale([0, xMax], [pad.l, width - pad.r]);
  const y = scale([0, 1], [height - pad.b, pad.t]);
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${yLabel} against ${xLabel}`}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={pad.l - 6} y={y(t) + 3} className="axis" textAnchor="end">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {[0, xMax / 4, xMax / 2, (3 * xMax) / 4, xMax].map((t) => (
          <text key={t} x={x(t)} y={height - pad.b + 14} className="axis" textAnchor="middle">
            {t % 1 === 0 ? t : t.toFixed(1)}
          </text>
        ))}
        <text x={(pad.l + width - pad.r) / 2} y={height - 4} className="axis-title" textAnchor="middle">
          {xLabel}
        </text>
        <text transform={`translate(12 ${(pad.t + height - pad.b) / 2}) rotate(-90)`} className="axis-title" textAnchor="middle">
          {yLabel}
        </text>
        {groups.map((g) => {
          const sorted = g.points.map((p, i) => ({ ...p, tip: g.tips[i] })).sort((a, b) => a.x - b.x);
          return (
            <g key={g.name}>
              <path d={sorted.map((p, i) => `${i ? "L" : "M"}${x(p.x)},${y(p.y)}`).join("")} className={`line ${g.className}`} />
              {sorted.map((p) => (
                <circle
                  key={`${p.x}-${p.y}-${p.tip}`}
                  cx={x(p.x)}
                  cy={y(p.y)}
                  r={4.5}
                  className={`dot ${g.className}`}
                  onMouseEnter={() => setTip(`${g.name}: ${p.tip}`)}
                  onMouseLeave={() => setTip(null)}
                  tabIndex={0}
                  onFocus={() => setTip(`${g.name}: ${p.tip}`)}
                  aria-label={`${g.name}: ${p.tip}`}
                />
              ))}
            </g>
          );
        })}
        {marker && (
          <g>
            <circle cx={x(marker.x)} cy={y(marker.y)} r={9} className="marker" />
            <text x={x(marker.x) - 12} y={y(marker.y) + 26} className="marker-label" textAnchor="end">
              {marker.label}
            </text>
          </g>
        )}
      </svg>
      <figcaption className="readout static">{tip ?? "Hover a point to see its threshold and results."}</figcaption>
    </figure>
  );
}

export function Heatmap({ labels, matrix, rowTitle, colTitle }: { labels: string[]; matrix: number[][]; rowTitle: string; colTitle: string }) {
  const max = Math.max(...matrix.flat(), 1);
  return (
    <div className="heatmap-wrap">
      <table className="heatmap">
        <caption className="sr-only">
          {rowTitle} by {colTitle}
        </caption>
        <thead>
          <tr>
            <th scope="col" className="corner">
              {rowTitle} ↓ / {colTitle} →
            </th>
            {labels.map((l) => (
              <th key={l} scope="col">
                {l}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={labels[i]}>
              <th scope="row">{labels[i]}</th>
              {row.map((v, j) => {
                const step = v === 0 ? 0 : Math.min(5, Math.ceil((v / max) * 5));
                return (
                  <td key={j} className={`h${step} ${i === j ? "diag" : ""}`} title={`${labels[i]} → ${labels[j]}: ${v}`}>
                    {v}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Bar({ value, max = 1, className = "", label }: { value: number; max?: number; className?: string; label?: ReactNode }) {
  return (
    <span className="bar" title={typeof label === "string" ? label : undefined}>
      <span className={`bar-fill ${className}`} style={{ width: `${Math.max(0, Math.min(1, value / max)) * 100}%` }} />
    </span>
  );
}
