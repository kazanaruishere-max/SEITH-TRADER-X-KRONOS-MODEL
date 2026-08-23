import { NextResponse } from "next/server";
import { listBacktests } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ backtests: listBacktests() });
}
