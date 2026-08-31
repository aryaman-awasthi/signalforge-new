import { NextRequest } from "next/server";

import { bootstrapSession } from "@/app/api/_lib/session";

export async function POST(request: NextRequest) {
  return bootstrapSession("/auth/accept-invite", await request.json());
}
