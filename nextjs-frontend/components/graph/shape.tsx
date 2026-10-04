import { edgeStyle, nodeShape, type GraphNode, type Shape } from "@/components/graph/model";
import { speakerTone } from "@/components/speakers/format";

/** Fill for a node: speakers in their speaker colour, entities in greys by type (organisations darkest). */
export function nodeFill(n: Pick<GraphNode, "kind" | "type" | "refs">): string {
  if (n.kind === "speaker") return speakerTone(n.refs[0]);
  if (n.kind === "recording") return "var(--aladdin-blue)";
  if (n.kind === "collection") return "var(--aladdin-gold)";
  if (n.kind === "namespace") return "var(--text-strong)";
  switch (n.type) {
    case "ORG":
    case "PERSON":
      return "var(--text-strong)";
    case "TERM":
      return "var(--text-muted)";
    default:
      return "var(--text-secondary)";
  }
}

/** The node's shape centred on (0, 0), `r` being half its size. */
export function ShapePath({
  shape,
  r,
  fill,
  stroke,
  strokeWidth = 2,
}: {
  shape: Shape;
  r: number;
  fill: string;
  stroke?: string;
  strokeWidth?: number;
}) {
  const common = { fill, stroke, strokeWidth };
  switch (shape) {
    case "circle":
      return <circle r={r} {...common} />;
    case "ring":
      return <circle r={r - 1} fill="var(--background)" stroke={fill} strokeWidth={Math.max(2, r * 0.35)} />;
    case "square":
      return <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={Math.max(2, r * 0.2)} {...common} />;
    case "rounded":
      return <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={r * 0.5} {...common} />;
    case "diamond":
      return (
        <rect
          x={-r * 0.78}
          y={-r * 0.78}
          width={r * 1.56}
          height={r * 1.56}
          rx={2}
          transform="rotate(45)"
          {...common}
        />
      );
    case "triangle":
      return (
        <path d={`M0 ${-r} L${r * 1.05} ${r * 0.8} L${-r * 1.05} ${r * 0.8} Z`} strokeLinejoin="round" {...common} />
      );
    case "pill":
      return <rect x={-r * 1.25} y={-r * 0.72} width={r * 2.5} height={r * 1.44} rx={r * 0.72} {...common} />;
    case "hexagon": {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i;
        return `${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`;
      }).join(" ");
      return <polygon points={pts} {...common} />;
    }
    case "octagon": {
      const pts = Array.from({ length: 8 }, (_, i) => {
        const a = (Math.PI / 4) * i + Math.PI / 8;
        return `${(Math.cos(a) * r).toFixed(2)},${(Math.sin(a) * r).toFixed(2)}`;
      }).join(" ");
      return <polygon points={pts} {...common} />;
    }
    case "doc":
      return (
        <path
          d={`M${-r * 0.75} ${-r} H${r * 0.35} L${r * 0.75} ${-r * 0.6} V${r} H${-r * 0.75} Z`}
          strokeLinejoin="round"
          {...common}
        />
      );
    case "folder":
      return (
        <path
          d={`M${-r} ${-r * 0.7} H${-r * 0.2} L${r * 0.05} ${-r * 0.45} H${r} V${r * 0.75} H${-r} Z`}
          strokeLinejoin="round"
          {...common}
        />
      );
  }
}

/** A small legend icon for a node type. */
export function ShapeIcon({
  shape,
  size = 12,
  fill = "var(--text-secondary)",
}: {
  shape: Shape;
  size?: number;
  fill?: string;
}) {
  return (
    <svg
      width={size + 4}
      height={size + 4}
      viewBox={`${-(size / 2 + 2)} ${-(size / 2 + 2)} ${size + 4} ${size + 4}`}
      aria-hidden
      className="shrink-0"
    >
      <ShapePath shape={shape} r={size / 2} fill={fill} />
    </svg>
  );
}

export function NodeIcon({ n, size = 16 }: { n: Pick<GraphNode, "kind" | "type" | "refs">; size?: number }) {
  return <ShapeIcon shape={nodeShape(n)} size={size} fill={nodeFill(n)} />;
}

/** A legend line for an edge kind: its dash pattern (and double line) as drawn on the canvas. */
export function EdgeIcon({ kind }: { kind: string }) {
  const s = edgeStyle(kind);
  const stroke = "gold" in s && s.gold ? "var(--aladdin-gold)" : "var(--text-secondary)";
  return (
    <svg width="22" height="8" aria-hidden className="shrink-0">
      {"double" in s && s.double ? (
        <>
          <line x1="0" y1="2.5" x2="22" y2="2.5" stroke={stroke} strokeWidth="1.3" />
          <line x1="0" y1="5.5" x2="22" y2="5.5" stroke={stroke} strokeWidth="1.3" />
        </>
      ) : (
        <line
          x1="0"
          y1="4"
          x2="22"
          y2="4"
          stroke={stroke}
          strokeWidth="2"
          strokeDasharray={"dash" in s ? s.dash : undefined}
        />
      )}
    </svg>
  );
}
