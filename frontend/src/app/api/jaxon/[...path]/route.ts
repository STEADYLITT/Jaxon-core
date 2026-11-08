import { NextRequest } from "next/server";

// Server-side proxy to the FastAPI backend. JAXON_API_URL / JAXON_API_TOKEN are read on the
// server only, so the token never reaches the browser.
const API_URL = process.env.JAXON_API_URL ?? "http://localhost:8000";
const ALLOWED_PREFIXES = ["health", "api/v1/"];

export const dynamic = "force-dynamic";

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!ALLOWED_PREFIXES.some((p) => joined === p || joined.startsWith(p))) {
    return Response.json({ error: { code: "not_found", message: "Unknown path" } }, { status: 404 });
  }
  const target = `${API_URL.replace(/\/$/, "")}/${joined}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "content-type": "application/json" };
  const token = process.env.JAXON_API_TOKEN;
  if (token) headers.authorization = `Bearer ${token}`;
  try {
    const res = await fetch(target, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.text(),
      cache: "no-store",
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { error: { code: "backend_unreachable", message: `Cannot reach JAXON API at ${API_URL}` } },
      { status: 502 },
    );
  }
}

export { proxy as GET, proxy as POST, proxy as DELETE };
