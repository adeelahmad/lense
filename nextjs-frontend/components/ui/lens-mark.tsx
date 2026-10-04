import type { SVGProps } from "react";

/* The Search Lens mark from the v1.0 brand kit: red, green and blue ring, blue memory core, gold handle.
   The fills are the kit's exact brand colours, which it uses on light and dark surfaces alike. */
const PATHS = [
  ["#EA4335", "M79.388 21.838A94 94 0 0 1 199.399 75.396L169.646 87.176A62 62 0 0 0 90.49 51.851Z"],
  ["#34A853", "M199.155 74.787A94 94 0 0 1 199.155 145.213A16 16 0 0 1 169.485 133.226A62 62 0 0 0 169.485 86.774Z"],
  ["#1A73E8", "M112 204A94 94 0 0 1 79.85 21.669A16 16 0 0 1 90.795 51.739A62 62 0 0 0 112 172A16 16 0 0 1 112 204Z"],
  ["#1A73E8", "M87 110A25 25 0 1 0 137 110A25 25 0 1 0 87 110Z"],
  [
    "#F9AB00",
    "M159.979 183.021L203.979 227.021A17 17 0 0 0 228.021 202.979L184.021 158.979A17 17 0 0 0 159.979 183.021Z",
  ],
] as const;

/** The Lens logo mark. Decorative unless given a label; monochrome follows the text colour. */
export function LensMark({
  size = 24,
  monochrome,
  label,
  ...props
}: SVGProps<SVGSVGElement> & { size?: number; monochrome?: boolean; label?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 256 256"
      width={size}
      height={size}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
      {...props}
    >
      {PATHS.map(([fill, d]) => (
        <path key={d} fill={monochrome ? "currentColor" : fill} d={d} />
      ))}
    </svg>
  );
}
