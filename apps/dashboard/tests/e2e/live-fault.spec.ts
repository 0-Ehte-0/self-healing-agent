import { test, expect } from "@playwright/test";
test.afterEach(async ({ page }) => {
  if (!process.env.M1_LIVE_FAULT) return;
  const me = await page.request.get("/api/v1/auth/me");
  if (me.ok()) {
    const user = await me.json();
    const result = await page.request.post("/api/v1/console/faults", {
      headers: { "X-CSRF-Token": user.csrf_token },
      data: {
        scenario_id: process.env.M1_LIVE_FAULT,
        action: "clear",
        idempotency_key: crypto.randomUUID(),
      },
    });
    console.log(`Fault cleanup HTTP ${result.status()}`);
  }
});
test("live M1 fault, versioned approval and independent resolution", async ({
  page,
}) => {
  const scenario = process.env.M1_LIVE_FAULT;
  test.skip(!scenario, "Opt-in controlled fault in the local demo-api");
  test.setTimeout(720000);
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "Username", exact: true })
    .fill(process.env.M1_USERNAME || "admin");
  await page
    .getByLabel("Password", { exact: true })
    .fill(process.env.M1_PASSWORD || "local-admin-change-me");
  await page.getByRole("button", { name: "Open console" }).click();
  await expect(
    page.getByRole("heading", { name: "Live environment" }),
  ).toBeVisible();
  const mode = await page.request.get("/api/v1/automation");
  if ((await mode.json()).mode !== "APPROVAL_REQUIRED") {
    await page.goto("/policies");
    await page.getByRole("button", { name: /Human approval/ }).click();
    await page.getByRole("textbox", { name: "Reason" }).fill("College demo rehearsal: review each recovery action");
    await page.getByRole("button", { name: "Change mode", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    await page.goto("/");
  }
  const before = await page.request.get("/api/v1/console/snapshot");
  const previous = new Set(
    (await before.json()).incidents.map((i: any) => i.id),
  );
  const card = page.locator(".scenario").filter({ hasText: scenario });
  await card.getByRole("button", { name: "Inject fault", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Inject fault", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "injected" }),
  ).toBeVisible({ timeout: 20000 });
  let incidentId: string | null = null;
  await expect
    .poll(
      async () => {
        const r = await page.request.get("/api/v1/console/snapshot");
        const s = await r.json();
        const inc = s.incidents.find(
          (i: any) =>
            !previous.has(i.id) && i.correlation_key.endsWith(`:${scenario}`),
        );
        if (inc) incidentId = inc.id;
        return !!incidentId;
      },
      { timeout: 240000, intervals: [5000] },
    )
    .toBeTruthy();
  console.log(`Live ${scenario} incident ${incidentId}`);
  await expect
    .poll(
      async () => {
        const r = await page.request.get(
          `/api/v1/console/incidents/${incidentId}`,
        );
        const d = await r.json();
        if (d.incident.state === "ESCALATED")
          throw new Error(
            `Escalated before approval: ${JSON.stringify(d.escalations)}`,
          );
        return d.incident.state;
      },
      { timeout: 120000, intervals: [3000] },
    )
    .toBe("PENDING_APPROVAL");
  await page.goto(`/incidents/${incidentId}`);
  await expect(
    page.getByRole("button", { name: "Approve plan", exact: true }),
  ).toBeVisible({ timeout: 120000 });
  await page.getByRole("tab", { name: "Plan & policy" }).click();
  await page.screenshot({
    path: `test-results/${scenario}-approval.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Approve plan", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve plan", exact: true })
    .click();
  await page.getByRole("tab", { name: "Verification", exact: true }).click();
  await expect
    .poll(
      async () => {
        const r = await page.request.get(
          `/api/v1/console/incidents/${incidentId}`,
        );
        const d = await r.json();
        if (d.incident.state === "ESCALATED")
          throw new Error(`Escalated: ${JSON.stringify(d.escalations)}`);
        return d.incident.state;
      },
      { timeout: 360000, intervals: [5000] },
    )
    .toBe("RESOLVED");
  const r = await page.request.get(`/api/v1/console/incidents/${incidentId}`);
  const detail = await r.json();
  expect(detail.verifications[0].passed).toBe(true);
  expect(detail.verifications[0].attribution).toBe("AGENT_HEALED");
  expect(detail.observations.length).toBeGreaterThan(0);
  console.log(
    `Verified ${scenario}: ${detail.verifications[0].samples.length} samples, ${detail.verifications[0].attribution}`,
  );
  await page.screenshot({
    path: `test-results/${scenario}-verified.png`,
    fullPage: true,
  });
});
