// Visual QA: authenticated screenshots of every AgentOS page (desktop + mobile).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const API = "http://127.0.0.1:8000/api/v1";
const APP = "http://localhost:3002";
const OUT = process.argv[2];

mkdirSync(OUT, { recursive: true });

const login = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "demo@example.com", password: "agentos-demo-123" }),
});
const { access_token } = await login.json();
const meResp = await fetch(`${API}/auth/me`, {
  headers: { Authorization: `Bearer ${access_token}` },
});
const me = await meResp.json();
const authState = JSON.stringify({
  state: { token: access_token, me, orgId: me.memberships[0].organization.id },
  version: 0,
});

// Resolve representative runs from live data.
const runsResp = await fetch(`${API}/runs?limit=50`, {
  headers: { Authorization: `Bearer ${access_token}` },
});
const runItems = (await runsResp.json()).items;
const COMPLETED_RUN = runItems.find((r) => r.status === "completed")?.id;
const PENDING_RUN = runItems.find((r) => r.status === "waiting_approval")?.id ?? COMPLETED_RUN;

const browser = await chromium.launch();

async function capture(viewport, suffix, routes) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(
    ([value]) => window.localStorage.setItem("agentos-auth", value),
    [authState],
  );
  const page = await context.newPage();
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  for (const [name, path] of routes) {
    await page.goto(`${APP}${path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/${name}${suffix}.png`, fullPage: name !== "run-pending" });
    console.log(`shot: ${name}${suffix}`);
  }
  if (errors.length) {
    console.log(`console errors (${suffix}):`);
    for (const err of errors.slice(0, 10)) console.log("  -", err.slice(0, 200));
  } else {
    console.log(`no console errors (${suffix})`);
  }
  await context.close();
}

const desktopRoutes = [
  ["overview", "/overview"],
  ["workspace", "/workspace"],
  ["runs", "/runs"],
  ["run-completed", `/runs/${COMPLETED_RUN}`],
  ["run-pending", `/runs/${PENDING_RUN}`],
  ["approvals", "/approvals"],
  ["knowledge", "/knowledge"],
  ["agents", "/agents"],
  ["tools", "/tools"],
  ["settings", "/settings"],
];
await capture({ width: 1440, height: 900 }, "", desktopRoutes);

// Result tab of the completed run
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(
    ([value]) => window.localStorage.setItem("agentos-auth", value),
    [authState],
  );
  const page = await context.newPage();
  await page.goto(`${APP}/runs/${COMPLETED_RUN}`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "Result" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/run-result.png`, fullPage: true });
  console.log("shot: run-result");
  await context.close();
}

// Login page without auth
{
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.screenshot({ path: `${OUT}/login.png` });
  console.log("shot: login");
  await context.close();
}

await capture({ width: 390, height: 844 }, "-mobile", [
  ["overview", "/overview"],
  ["run-completed", `/runs/${COMPLETED_RUN}`],
]);

// Mobile with the nav opened
{
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await context.addInitScript(
    ([value]) => window.localStorage.setItem("agentos-auth", value),
    [authState],
  );
  const page = await context.newPage();
  await page.goto(`${APP}/overview`, { waitUntil: "networkidle" });
  await page.getByLabel("Open navigation").click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/nav-mobile.png` });
  console.log("shot: nav-mobile");
  await context.close();
}

await browser.close();
console.log("done");
