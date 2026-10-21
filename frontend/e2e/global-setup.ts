import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";

/**
 * End-to-end setup.
 *
 * 1. Brings up Meilisearch, Tor and the backend with `docker compose up -d
 *    --build` (skipped with E2E_SKIP_STACK=1 when the stack is already running).
 * 2. Waits until the backend reports Meilisearch as `up`.
 * 3. Copies the Katana fixture into `data/` and ingests it with the real CLI:
 *    the fixture pages are indexed exactly as a crawl would be.
 *
 * The fixture uses a dedicated domain (`e2e.example`) and a single word
 * (`aegisfixture`), so the specs can filter on the domain and count results
 * without depending on whatever else the developer has in the index.
 */
function findRepoRoot(start: string): string {
  let current = start;
  for (let depth = 0; depth < 5; depth += 1) {
    if (existsSync(path.join(current, "docker-compose.yml"))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  throw new Error(
    `Cannot find docker-compose.yml above ${start}. Run the suite from the frontend directory.`,
  );
}

const repoRoot = findRepoRoot(process.cwd());
const frontendRoot = path.join(repoRoot, "frontend");
const apiBase = (process.env.E2E_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const healthTimeoutMs = Number(process.env.E2E_HEALTH_TIMEOUT_MS ?? 180_000);
const fixtureName = "e2e-katana.jsonl";

function docker(args: string[]): void {
  try {
    execFileSync("docker", args, { cwd: repoRoot, stdio: "inherit" });
  } catch (error) {
    throw new Error(
      `Cannot run "docker ${args.join(" ")}" from ${repoRoot}. ` +
        "Start Docker Desktop, or set E2E_SKIP_STACK=1 to reuse an already running stack.",
      { cause: error },
    );
  }
}

async function waitForBackend(): Promise<void> {
  const deadline = Date.now() + healthTimeoutMs;
  let lastReason = "no answer";

  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${apiBase}/api/v1/health`, { cache: "no-store" });
      if (response.ok) {
        const payload = (await response.json()) as { meilisearch?: string };
        if (payload.meilisearch === "up") {
          return;
        }
        lastReason = `meilisearch=${payload.meilisearch ?? "unknown"}`;
      } else {
        lastReason = `HTTP ${response.status}`;
      }
    } catch (error) {
      lastReason = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }

  throw new Error(
    `Backend not ready at ${apiBase} after ${Math.round(healthTimeoutMs / 1000)}s (${lastReason}). ` +
      "Run `docker compose up -d --build` from the repository root and retry.",
  );
}

function seedIndex(): void {
  const dataDir = path.join(repoRoot, "data");
  mkdirSync(dataDir, { recursive: true });
  copyFileSync(
    path.join(frontendRoot, "e2e", "fixtures", "katana.jsonl"),
    path.join(dataDir, fixtureName),
  );

  docker([
    "compose",
    "exec",
    "-T",
    "backend",
    "python",
    "-m",
    "app.ingestion.cli",
    "--input",
    `/data/${fixtureName}`,
    // The fixture pages are intentionally similar: keep every one of them so
    // the pagination assertion has a stable document count.
    "--no-dedupe",
  ]);
}

export default async function globalSetup(): Promise<void> {
  if (process.env.E2E_SKIP_STACK !== "1") {
    docker(["compose", "up", "-d", "--build"]);
  }

  await waitForBackend();
  seedIndex();
}
