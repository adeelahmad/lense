import type { MetadataRoute } from "next";

/* The installable-app manifest, from the brand kit's site.webmanifest. Icons live in public/pwa. */
export default function manifest(): MetadataRoute.Manifest {
  const icon = (purpose: "any" | "maskable", size: number) => ({
    src: `/pwa/lens-light-${purpose}-${size}.png`,
    sizes: `${size}x${size}`,
    type: "image/png",
    purpose,
  });
  return {
    id: "/",
    name: "Lens",
    short_name: "Lens",
    description: "Your life. Your AI.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#FFFFFF",
    theme_color: "#1A73E8",
    icons: [icon("any", 192), icon("any", 512), icon("maskable", 192), icon("maskable", 512)],
  };
}
