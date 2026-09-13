import { test, expect, Page } from "@playwright/test";
const id = "11111111-1111-4111-8111-111111111111";
const resource = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "demo-api",
  environment: "local",
  provider: "docker",
  managed: true,
  labels: {
    compose_service: "demo-api",
    docker_container_id: "a".repeat(64),
    binding_generation: 1,
  },
};
async function setup(page: Page, role = "admin") {
  let state = "PENDING_APPROVAL",
    mode = "APPROVAL_REQUIRED",
    expired = false;
  const mutations: any[] = [];
  const time = new Date().toISOString();
  const inc = () => ({
    id,
    resource_id: resource.id,
    correlation_key: "SCN-002 / demo-api",
    state,
    severity: "HIGH",
    version: 5,
    attempts: 0,
    retry_limit: 2,
    created_at: time,
    updated_at: time,
  });
  const plan = {
    id: "33333333-3333-4333-8333-333333333333",
    version: 1,
    content_hash: "b".repeat(64),
    container_id: "a".repeat(64),
    binding_generation: 1,
    risk: "LOW",
    verification_profile: "SCN-002",
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    const post = route.request().method() === "POST";
    const body = post ? route.request().postDataJSON() : null;
    if (post)
      mutations.push({
        path,
        body,
        csrf: route.request().headers()["x-csrf-token"],
      });
    const send = (data: any, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    if (path === "/auth/me")
      return send(
        expired
          ? { detail: "Session expired" }
          : { username: "demo-admin", role, csrf_token: "csrf-fixture" },
        expired ? 401 : 200,
      );
    if (path === "/console/stream") return route.abort();
    if (path === "/console/snapshot")
      return send(
        expired
          ? { detail: "Session expired" }
          : {
              server_time: new Date().toISOString(),
              cursor: 20,
              incidents: [inc()],
              counts: { [state]: 1, RESOLVED: 4 },
              resources: [resource],
              automation: {
                mode,
                emergency_stopped_resources: [],
                updated_by: "user:admin",
                reason: "Demo configuration",
              },
              demo_controls_enabled: true,
            },
        expired ? 401 : 200,
      );
    if (path === "/console/telemetry") {
      const points = Array.from({ length: 40 }, (_, i) => [
        Date.now() / 1000 - (39 - i) * 15,
        8 + Math.sin(i) * 2,
      ]);
      return send({
        server_time: new Date().toISOString(),
        signals: Object.fromEntries(
          ["rps", "errors", "cpu", "latency", "ready", "redis"].map((k) => [
            k,
            {
              latest: ["ready", "redis"].includes(k) ? 1 : 8,
              available: true,
              points,
            },
          ]),
        ),
      });
    }
    if (path === "/console/faults" && !post)
      return send({ available: true, active: [] });
    if (path === "/console/faults" && post)
      return send({ status: "SUCCEEDED", result: { status: "injected" } });
    if (path === `/console/incidents/${id}`)
      return send({
        server_time: new Date().toISOString(),
        incident: inc(),
        resource,
        plans: [plan],
        steps: [
          {
            id: "step",
            plan_id: plan.id,
            position: 0,
            action: "restart_container",
            parameters: { container_id: plan.container_id },
            verification: {},
          },
        ],
        diagnoses: [
          {
            root_cause: "CPU_SATURATION",
            confidence: 0.97,
            reasoning: {
              summary: "Sustained CPU pressure aligns with the injected fault.",
            },
          },
        ],
        policy_decisions: [
          {
            id: "policy-decision",
            decision: "REQUIRE_APPROVAL",
            reason_codes: ["AUTOMATION_APPROVAL_REQUIRED"],
            policy_version: 1,
            plan_version: 1,
          },
        ],
        evidence: [
          {
            id: "evidence",
            kind: "METRICS",
            source: "prometheus",
            observed_at: time,
            unit: "%",
            binding_generation: 1,
            content: { cpu: 97 },
            sha256: "c".repeat(64),
          },
        ],
        events: [],
        approvals: [],
        executions: [],
        verifications: [],
        attention: [],
        escalations: [],
        schedules: [],
        observations: [],
        dry_runs: [],
      });
    if (path === `/console/incidents/${id}/timeline`)
      return send({
        items: [
          {
            id: "audit",
            sequence: 20,
            entity_type: "incidents",
            operation: "UPDATE",
            actor: "worker:demo",
            created_at: time,
            details: { before: { state: "PLANNED" }, after: { state } },
          },
        ],
        next_cursor: null,
      });
    if (path === `/incidents/${id}/approval`) {
      state = body.decision === "APPROVE" ? "APPROVED" : "ESCALATED";
      return send({ incident_state: state });
    }
    if (path === "/automation/mode") {
      mode = body.mode;
      return send({ mode });
    }
    if (path === "/console/policies") return send({ items: [] });
    if (path === "/console/resources")
      return send({ items: [resource], locks: [] });
    if (path === "/console/incidents")
      return send({ items: [inc()], total: 1 });
    if (path === "/console/system")
      return send({
        database_connected: true,
        redis_connected: true,
        outbox_backlog: 0,
        worker_heartbeats: [],
      });
    if (path === "/console/commands") return send({ items: [] });
    if (path.endsWith("/dry-run"))
      return send({ validation_result: { valid: true } });
    return send({});
  });
  return {
    mutations,
    setState: (value: string) => {
      state = value;
    },
    expire: () => {
      expired = true;
    },
  };
}

test("demo lab renders topology, charts and confirms a bounded fault command", async ({
  page,
}) => {
  const { mutations } = await setup(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Resilience, in real time." }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Inject fault/ })).toHaveCount(
    3,
  );
  await page
    .getByRole("button", { name: /Traffic generator Continuous/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Traffic generator" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/demo-lab-desktop.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: /Inject fault/ })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Inject fault", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Container crash: injected" }),
  ).toBeVisible();
  expect(mutations[0].body.scenario_id).toBe("SCN-001");
  expect(mutations[0].csrf).toBe("csrf-fixture");
  expect(mutations[0].body.idempotency_key).toBeTruthy();
});
test("viewer observes without mutation controls", async ({ page }) => {
  await setup(page, "viewer");
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /Inject fault/ }).first(),
  ).toBeDisabled();
  await page.goto(`/incidents/${id}`);
  await expect(
    page.getByText("An approver or administrator must decide."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve plan", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Verification", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Independent recovery verification" }),
  ).toBeVisible();
});
test("approver reviews and approves the versioned plan", async ({ page }) => {
  const { mutations } = await setup(page, "approver");
  await page.goto(`/incidents/${id}`);
  await page.getByRole("tab", { name: "Plan & policy" }).click();
  await expect(
    page.getByText("require approval", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Approve plan", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve plan" })
    .click();
  await expect(page.getByText("Plan approved", { exact: true })).toBeVisible();
  expect(mutations[0].body).toMatchObject({
    expected_incident_version: 5,
    plan_version: 1,
    content_hash: "b".repeat(64),
  });
  await page.getByRole("tab", { name: "Audit timeline" }).click();
  await expect(page.getByText("#20 · worker:demo")).toBeVisible();
  await page.screenshot({
    path: "test-results/incident-workspace.png",
    fullPage: true,
  });
});
test("rejection requires a reason and stale approval refreshes instead of implying success", async ({
  page,
}) => {
  await setup(page, "approver");
  await page.route(`**/api/v1/incidents/${id}/approval`, (r) =>
    r.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "Incident version changed. Refresh and review.",
      }),
    }),
  );
  await page.goto(`/incidents/${id}`);
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("button", { name: "Reject plan" }),
  ).toBeDisabled();
  await dialog
    .getByRole("textbox", { name: "Reason" })
    .fill("Need further evidence");
  await dialog.getByRole("button", { name: "Reject plan" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "Incident version changed" }),
  ).toBeVisible();
  await expect(page.getByText("Plan rejected", { exact: true })).toHaveCount(0);
});
test("administrator pauses automation through a reasoned confirmation", async ({
  page,
}) => {
  const { mutations } = await setup(page);
  await page.goto("/policies");
  await page.getByRole("button", { name: /Paused Observe/ }).click();
  await page
    .getByRole("textbox", { name: "Reason" })
    .fill("Pause for classroom explanation");
  await page.getByRole("button", { name: "Change mode", exact: true }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(mutations[0].body).toMatchObject({
    mode: "DISABLED",
    reason: "Pause for classroom explanation",
  });
});
test("session expiry removes protected data and offers login", async ({
  page,
}) => {
  const fixture = await setup(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Live environment" }),
  ).toBeVisible();
  fixture.expire();
  await page.getByRole("button", { name: "Refresh data" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to the lab" }),
  ).toBeVisible();
  await expect(
    page.getByText("Your session expired.", { exact: false }),
  ).toBeVisible();
});
test("narrow layout, keyboard focus and reduced motion remain usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Live environment" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to content" }),
  ).toBeFocused();
  await page
    .getByRole("button", { name: /Inject fault/ })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.screenshot({
    path: "test-results/demo-lab-mobile.png",
    fullPage: true,
  });
});
test("missing telemetry is explicit and stops packet motion", async ({
  page,
}) => {
  await setup(page);
  await page.route("**/api/v1/console/telemetry**", (r) =>
    r.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        server_time: new Date().toISOString(),
        signals: {},
      }),
    }),
  );
  await page.goto("/");
  await expect(page.getByText("Waiting for telemetry")).toHaveCount(4);
  await expect(page.locator(".packets")).toHaveCount(0);
});

test("viewer catches up after stream loss and repeated invalidations do not duplicate audit rows", async ({
  page,
}) => {
  await page.addInitScript(() => {
    class TestStream extends EventTarget {
      onerror: (() => void) | null = null;
      constructor() {
        super();
        (window as any).testStream = this;
      }
      close() {}
    }
    (window as any).EventSource = TestStream;
  });
  const fixture = await setup(page, "viewer");
  await page.goto(`/incidents/${id}`);
  await page.getByRole("tab", { name: "Audit timeline", exact: true }).click();
  await expect(page.locator(".audit-timeline article")).toHaveCount(1);
  await page.evaluate(() => (window as any).testStream.onerror());
  await expect(
    page.getByText("Polling fallback", { exact: true }),
  ).toBeVisible();
  fixture.setState("VERIFYING");
  await expect(page.locator(".audit-timeline article")).toContainText(
    "VERIFYING",
    { timeout: 10000 },
  );
  fixture.setState("RESOLVED");
  await page.evaluate(() => {
    for (let i = 0; i < 3; i++)
      (window as any).testStream.dispatchEvent(
        new MessageEvent("snapshot", { data: JSON.stringify({ cursor: 99 }) }),
      );
  });
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await expect(page.locator(".audit-timeline article")).toContainText(
    "RESOLVED",
    { timeout: 10000 },
  );
  await expect(page.locator(".audit-timeline article")).toHaveCount(1);
  expect(fixture.mutations).toHaveLength(0);
});
