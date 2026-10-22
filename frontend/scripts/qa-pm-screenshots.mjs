// Visual QA for the PM harness pages (projects list, detail states, board, task dialog).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const API = "http://127.0.0.1:8000/api/v1";
const APP = process.env.APP_URL ?? "http://localhost:3002";
const OUT = process.argv[2];

mkdirSync(OUT, { recursive: true });

const login = await fetch(`${API}/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "demo@example.com", password: "agentos-demo-123" }),
});
const { access_token } = await login.json();
const me = await (
  await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${access_token}` } })
).json();
const authState = JSON.stringify({
  state: { token: access_token, me, orgId: me.memberships[0].organization.id },
  version: 0,
});

const projects = await (
  await fetch(`${API}/projects`, { headers: { Authorization: `Bearer ${access_token}` } })
).json();
const AWAITING = projects.find((p) => p.status === "awaiting_approval")?.id;
const COMPLETED = projects.find((p) => p.status === "completed")?.id;

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.addInitScript(
  ([value]) => window.localStorage.setItem("agentos-auth", value),
  [authState],
);
const page = await context.newPage();
const errors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});

await page.goto(`${APP}/projects`, { waitUntil: "networkidle" });
await page.screenshot({ path: `${OUT}/projects.png`, fullPage: true });
console.log("shot: projects");

if (COMPLETED) {
  await page.goto(`${APP}/projects/${COMPLETED}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/project-completed.png`, fullPage: true });
  console.log("shot: project-completed (workflow)");

  await page.getByRole("tab", { name: "Board" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/project-board.png`, fullPage: true });
  console.log("shot: project-board");

  await page.getByRole("tab", { name: "Timeline" }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/project-timeline.png`, fullPage: true });
  console.log("shot: project-timeline");

  // Open a task node from the board for the details dialog.
  await page.getByRole("tab", { name: "Board" }).click();
  await page.waitForTimeout(300);
  await page.locator("button:has-text('#2')").first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/task-details.png` });
  console.log("shot: task-details");
  await page.keyboard.press("Escape");
}

if (AWAITING) {
  await page.goto(`${APP}/projects/${AWAITING}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/project-plan-approval.png`, fullPage: true });
  console.log("shot: project-plan-approval");
}

// Mobile check for the project detail page.
const mobile = await browser.newContext({ viewport: { width: 390, height: 844 } });
await mobile.addInitScript(
  ([value]) => window.localStorage.setItem("agentos-auth", value),
  [authState],
);
const mpage = await mobile.newPage();
await mpage.goto(`${APP}/projects/${COMPLETED ?? AWAITING}`, { waitUntil: "networkidle" });
await mpage.waitForTimeout(700);
await mpage.screenshot({ path: `${OUT}/project-mobile.png`, fullPage: true });
console.log("shot: project-mobile");

if (errors.length) {
  console.log("console errors:");
  for (const err of errors.slice(0, 10)) console.log("  -", err.slice(0, 200));
} else {
  console.log("no console errors");
}
await browser.close();
console.log("done");
