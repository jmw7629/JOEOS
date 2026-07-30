import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = resolve(root, "index.html");
const outputDirectory = resolve(root, "frontend_dist");
const outputPath = resolve(outputDirectory, "index.html");

const html = await readFile(sourcePath, "utf8");

await mkdir(outputDirectory, { recursive: true });
await writeFile(outputPath, html, "utf8");
