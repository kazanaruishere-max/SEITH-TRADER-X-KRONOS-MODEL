import { NextResponse } from "next/server";
import { listDecisions } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ decisions: listDecisions(20) });
}
