import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const port = Number(process.env.PORT ?? 8081);

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".map": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json; charset=utf-8",
};

async function resolvePath(urlPath) {
  const relative = normalize(decodeURIComponent(urlPath)).replace(/^(\.\.[/\\])+/, "");
  const candidate = join(dist, relative === "/" ? "index.html" : relative);
  try {
    const info = await stat(candidate);
    if (info.isFile()) return candidate;
    if (info.isDirectory()) {
      const index = join(candidate, "index.html");
      if ((await stat(index)).isFile()) return index;
    }
  } catch {
    return null;
  }
  return null;
}

const server = createServer(async (req, res) => {
  const path = await resolvePath(req.url ?? "/");
  if (!path) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("Not found");
    return;
  }
  res.writeHead(200, { "Content-Type": types[extname(path)] ?? "application/octet-stream" });
  createReadStream(path).pipe(res);
});

server.listen(port, () => {
  console.log(`JoeOS mobile web client serving ${dist} at http://localhost:${port}`);
});
