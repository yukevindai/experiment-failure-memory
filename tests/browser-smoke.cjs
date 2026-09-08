/* Explicit opt-in smoke test for a disposable, already running instance. */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  for (const name of ["EFM_TEST_URL", "EFM_TEST_USER", "EFM_TEST_PASSWORD"])
    assert(process.env[name], `${name} is required`);
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({
      viewport: { width: 1365, height: 1000 },
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(process.env.EFM_TEST_URL);
    await page
      .getByLabel("Username", { exact: true })
      .first()
      .fill(process.env.EFM_TEST_USER);
    await page
      .getByLabel("Password", { exact: true })
      .fill(process.env.EFM_TEST_PASSWORD);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await page.locator("#workspace").waitFor({ state: "visible" });
    await page
      .getByRole("button", { name: "Access & exchange", exact: true })
      .click();
    await page.locator("#lab-form input").fill("Synthetic browser lab");
    await page
      .getByRole("button", { name: "Create laboratory", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector("#lab").options.length > 0,
    );
    await page
      .locator("#project-form input")
      .fill("Synthetic electrolyte study");
    await page
      .getByRole("button", { name: "Create project", exact: true })
      .click();
    await page.waitForFunction(
      () =>
        document.querySelector("#project").value !== "" &&
        !document.querySelector("#new-record").disabled,
    );
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Record an experiment", exact: true })
      .click();
    await page
      .getByLabel("Experiment title", { exact: true })
      .fill("Synthetic precipitation failure");
    await page
      .getByLabel("Observed outcomes", { exact: true })
      .fill("Synthetic precipitate observed after mixing.");
    await page
      .getByLabel("Uncertainty and limitations", { exact: true })
      .fill("Synthetic smoke test only.");
    await page
      .getByLabel("Source reference", { exact: true })
      .fill("SYNTHETIC notebook page 1");
    await page
      .getByRole("button", { name: "Add conditions", exact: true })
      .click();
    const condition = page.locator('[data-entry="conditions"]');
    await condition.locator('[data-field="name"]').fill("temperature");
    await condition.locator('[data-field="value"]').fill("25");
    await condition.locator('[data-field="unit"]').fill("degC");
    await page
      .getByRole("button", { name: "Save experiment", exact: true })
      .click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    await page.locator("#detail h2").waitFor({ state: "visible" });
    assert.equal(
      await page.locator("#detail h2").textContent(),
      "Synthetic precipitation failure",
    );
    await page
      .getByRole("button", { name: "Edit experiment", exact: true })
      .click();
    await page
      .getByLabel("Experiment title", { exact: true })
      .fill("Synthetic precipitation failure — revised");
    await page
      .getByRole("button", { name: "Save experiment", exact: true })
      .click();
    await page.locator("#editor").waitFor({ state: "hidden" });
    await page.waitForFunction(() =>
      document.querySelector("#detail h2")?.textContent.includes("revised"),
    );
    await page
      .getByLabel("Comment", { exact: true })
      .fill("Follow-up: repeat under dry conditions.");
    await page
      .getByRole("button", { name: "Add comment", exact: true })
      .click();
    await page
      .getByText("Follow-up: repeat under dry conditions.", { exact: true })
      .waitFor();
    await page
      .getByLabel("Attach a file", { exact: true })
      .setInputFiles({
        name: "synthetic.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("SYNTHETIC ATTACHMENT"),
      });
    await page
      .getByRole("button", { name: "Upload attachment", exact: true })
      .click();
    await page
      .getByRole("link", { name: "synthetic.txt (20 bytes)", exact: true })
      .waitFor();
    await page
      .getByRole("button", { name: "Patterns & recovery", exact: true })
      .click();
    await page.getByText("1 active records", { exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Access & exchange", exact: true })
      .click();
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Download project export", exact: true })
      .click();
    assert.equal(
      (await downloadPromise).suggestedFilename(),
      "experiment-memory.zip",
    );
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    await page.setViewportSize({ width: 390, height: 844 });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      "Mobile horizontal overflow",
    );
    await page.setViewportSize({ width: 1365, height: 1000 });
    if (process.env.EFM_SCREENSHOT)
      await page.screenshot({
        path: process.env.EFM_SCREENSHOT,
        fullPage: true,
      });
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await page.locator("#login-panel").waitFor({ state: "visible" });
    assert.deepEqual(errors, []);
    console.log(
      "Browser smoke passed: login, lab/project, record, revision, comment, attachment, patterns, export, mobile layout, logout.",
    );
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
