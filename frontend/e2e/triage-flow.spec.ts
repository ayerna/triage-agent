import { test, expect } from "@playwright/test";

test.describe("Incident Triage End-to-End Flow", () => {
  test("sign in, file incident, run triage, and approve", async ({ page }) => {
    // 1. Visit app and verify Sign In screen
    await page.goto("/");
    await expect(page.locator("h2")).toContainText("Incident Triage Agent");

    // 2. Perform Sign In
    await page.fill('[data-testid="login-email"]', "engineer@vessel-demo.example.com");
    await page.fill('[data-testid="login-password"]', "demo-pass-1");
    await page.click('[data-testid="login-submit"]');

    // 3. Verify Incident List screen
    await expect(page.locator('[data-testid="incidents-heading"]')).toBeVisible({ timeout: 10000 });

    // 4. File a New Incident
    await page.click('[data-testid="new-incident-btn"]');
    await expect(page.locator("h3")).toContainText("File Synthetic Incident");

    const testTitle = `[SYNTHETIC E2E] Main Bilge Valve Vibration ${Date.now()}`;
    const testDesc = "High frequency vibration detected on bilge line check valve BV-04 during transfer. Seal integrity intact.";

    await page.fill('[data-testid="incident-title-input"]', testTitle);
    await page.fill('[data-testid="incident-desc-input"]', testDesc);
    await page.click('[data-testid="incident-submit-btn"]');

    // 5. Verify Incident Detail screen loaded
    await expect(page.locator("h2")).toContainText(testTitle);
    await expect(page.locator(".badge-open")).toBeVisible();

    // 6. Run Triage Agent
    const triageBtn = page.locator('[data-testid="run-triage-btn"]');
    await expect(triageBtn).toBeEnabled();
    await triageBtn.click();

    // 7. Verify Agent Steps and Proposal appeared
    await expect(page.locator('[data-testid="agent-steps-container"]')).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole("heading", { name: "Triage Proposal" })).toBeVisible();
    await expect(page.locator(".badge-triaged")).toBeVisible();

    // 8. Human Decision: Approve with note
    await page.fill('[data-testid="decision-note"]', "Confirmed precedent matches; approved for scheduled repair.");
    const approveBtn = page.locator('[data-testid="approve-btn"]');
    await expect(approveBtn).toBeEnabled();
    await approveBtn.click();

    // 9. Verify Decision Completed
    await expect(page.locator("text=Successfully recorded: Approved")).toBeVisible({ timeout: 10000 });
    await expect(page.locator(".badge-approved")).toBeVisible();
  });
});
