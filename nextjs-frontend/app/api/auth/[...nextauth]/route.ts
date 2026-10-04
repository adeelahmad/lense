import type { NextRequest } from "next/server";

import { handlers } from "@/auth";
import { atBrowserOrigin } from "@/lib/auth/browser-origin";

export const GET = (req: NextRequest) => handlers.GET(atBrowserOrigin(req));
export const POST = (req: NextRequest) => handlers.POST(atBrowserOrigin(req));
