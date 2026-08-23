import { NextRequest, NextResponse } from "next/server";
import { tearsheetHtml } from "@/lib/data";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const ticker = req.nextUrl.searchParams.get("ticker") ?? "";
  const timeframe = req.nextUrl.searchParams.get("tf") ?? "1h";
  if (!/^[A-Z0-9._-]{1,20}$/i.test(ticker) || !/^[a-z0-9]{1,4}$/i.test(timeframe)) {
    return new NextResponse("invalid params", { status: 400 });
  }
  const html = tearsheetHtml(ticker, timeframe);
  if (html === null) return new NextResponse("not found", { status: 404 });
  return new NextResponse(html, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
