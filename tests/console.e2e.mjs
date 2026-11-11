import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { chromium } from "playwright";

// Complete story: login -> create resource -> durable worker -> ready UI ->
// object upload/download -> secret access -> mobile navigation -> sign out.
const python = process.env.CLOUDER_TEST_PYTHON || "python";
const data = mkdtempSync(join(tmpdir(), "clouder-e2e-"));
const port = process.env.CLOUDER_TEST_PORT || "8765";
const base = `http://127.0.0.1:${port}`;
const env = {
  ...process.env,
  CLOUDER_DATABASE_URL: `sqlite:///${data}/metadata.db`,
  CLOUDER_DATA_DIR: data,
  CLOUDER_ENCRYPTION_KEY: randomBytes(32).toString("base64url") + "=",
  CLOUDER_PROVIDER: "simulated",
};
const initialized = spawnSync(
  python,
  [
    "-c",
    `
from clouder.config import Settings
from clouder.db import Database
from clouder.models import Tenant
from clouder.security import issue_key
db=Database(Settings()); db.migrate()
with db.transaction() as session:
    tenant=Tenant(name="Engineering workspace"); session.add(tenant); session.flush()
    _, token=issue_key(session, tenant.id, "browser-test", "owner", 1)
print(token)
`,
  ],
  { env, encoding: "utf8" },
);
assert.equal(initialized.status, 0, initialized.stderr);
const token = initialized.stdout.trim();
const logs = [];
const server = spawn(python, ["-m", "clouder.cli", "serve", "--port", port], {
  env,
});
const worker = spawn(python, ["-m", "clouder.cli", "worker"], { env });
for (const child of [server, worker])
  child.stderr.on("data", (chunk) => logs.push(chunk.toString()));
let browser;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitUntil(fn, description, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    try {
      if (await fn()) return;
    } catch {}
    await delay(150);
  }
  throw new Error(description);
}
try {
  await waitUntil(
    async () => (await fetch(base + "/health/ready")).ok,
    "API did not become ready",
  );
  browser = await chromium.launch({
    headless: true,
    ...(process.env.CLOUDER_CHROMIUM_PATH
      ? {
          executablePath: process.env.CLOUDER_CHROMIUM_PATH,
          args: [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
            "--single-process",
          ],
        }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto(base);
  await page.getByLabel("Workspace API key").fill(token);
  await page.getByRole("button", { name: "Open console" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Create resource", exact: false })
    .click();
  await page.getByLabel("Resource name").fill("browser-web");
  await page.locator("#modal-submit").click();
  await waitUntil(
    () =>
      page
        .locator("#notice")
        .textContent()
        .then((x) => x.includes("Provisioning accepted")),
    "Create request was not accepted",
  );
  await waitUntil(async () => {
    const response = await fetch(base + "/v1/resources", {
      headers: { Authorization: `Bearer ${token}` },
    });
    return (await response.json()).items.some(
      (x) => x.name === "browser-web" && x.state === "ready",
    );
  }, "Worker did not provision compute");
  await page.getByRole("button", { name: "Refresh", exact: false }).click();
  await page
    .getByRole("cell", { name: "ready", exact: true })
    .first()
    .waitFor();
  await page.locator('nav a[href="#bucket"]').click();
  await page
    .getByRole("button", { name: "Create resource", exact: false })
    .click();
  await page.getByLabel("Resource name").fill("browser-assets");
  await page.locator("#modal-submit").click();
  await page
    .getByRole("button", { name: "Upload object", exact: false })
    .waitFor({ timeout: 15000 });
  await page
    .getByRole("button", { name: "Upload object", exact: false })
    .click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "proof.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("CloudER browser round trip"),
  });
  await page.locator("#modal-submit").click();
  await page.getByRole("cell", { name: "proof.txt", exact: true }).waitFor();
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const download = await downloadEvent;
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString(), "CloudER browser round trip");
  await page.locator('nav a[href="#secrets"]').click();
  await page
    .getByRole("button", { name: "Store secret", exact: false })
    .click();
  await page.getByLabel("Name", { exact: true }).fill("browser-secret");
  await page
    .getByLabel("Secret value", { exact: true })
    .fill("test-only-secret-value");
  await page.locator("#modal-submit").click();
  await page.getByRole("button", { name: "Reveal", exact: true }).click();
  await page.locator(".secret-value").waitFor();
  assert.equal(
    await page.locator(".secret-value").textContent(),
    "test-only-secret-value",
  );
  await page.locator("#cancel-modal").click();
  assert.equal(await page.locator(".secret-value").count(), 0);
  for (const section of [
    "database",
    "loadbalancer",
    "keys",
    "monitoring",
    "billing",
    "audit",
    "operations",
    "overview",
  ]) {
    await page.locator(`nav a[href="#${section}"]`).click();
    await page.locator(`#content[data-view="${section}"]`).waitFor();
    assert.equal(
      await page.locator("#notice.error").count(),
      0,
      `Error in ${section}`,
    );
  }
  mkdirSync("test-results", { recursive: true });
  await page.screenshot({
    path: "test-results/console-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
    "Mobile page overflows horizontally",
  );
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await page.locator('nav a[href="#compute"]').click();
  await page.getByRole("heading", { name: "Compute", exact: true }).waitFor();
  await page.locator('#content[data-view="compute"]').waitFor();
  await page.screenshot({
    path: "test-results/console-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  assert.deepEqual(
    await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
    { local: 0, session: 0 },
  );
  assert.deepEqual(errors, [], "Browser console errors");
  assert.ok(!logs.join("").includes(token), "API key leaked to process logs");
  assert.ok(
    !logs.join("").includes("test-only-secret-value"),
    "Secret leaked to process logs",
  );
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Workspace API key").waitFor();
  assert.equal(await page.locator("#content").textContent(), "");
  console.log(
    "PASS: desktop/mobile console, compute provisioning, object round trip, secret reveal, navigation, sign-out, storage isolation, and zero browser errors",
  );
} catch (error) {
  console.error(error.message);
  // Logs are retained in memory only; do not dump possible credentials on failure.
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  for (const child of [worker, server]) {
    const exit = new Promise((resolve) => child.once("exit", resolve));
    child.kill("SIGTERM");
    await Promise.race([exit, delay(3000)]);
    if (child.exitCode === null) child.kill("SIGKILL");
  }
  rmSync(data, { recursive: true, force: true });
}
