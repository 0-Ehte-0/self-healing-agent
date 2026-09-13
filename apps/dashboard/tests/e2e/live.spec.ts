import { test, expect } from "@playwright/test";
test("live Compose login, read models, telemetry and session logout", async ({
  page,
}) => {
  test.skip(
    process.env.M1_LIVE !== "1",
    "Opt-in test against the running local Compose stack",
  );
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
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
  ).toBeVisible({ timeout: 20000 });
  await expect(page.getByText("Waiting for telemetry")).toHaveCount(0, {
    timeout: 20000,
  });
  await page.screenshot({
    path: "test-results/live-demo-lab.png",
    fullPage: true,
  });
  for (const path of ["/incidents", "/resources", "/policies", "/system"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator(".notice.bad")).toHaveCount(0);
  }
  await expect(page.getByText("Connected", { exact: true })).toHaveCount(2, {
    timeout: 10000,
  });
  expect(errors).toEqual([]);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome to the lab" }),
  ).toBeVisible();
});
