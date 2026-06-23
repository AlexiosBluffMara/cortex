import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const BASE_URL = process.env.CORTEX_E2E_BASE_URL || "http://127.0.0.1:8765";
const DEFAULT_CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const CHROME_PATH = process.env.CHROME_PATH || DEFAULT_CHROME;
const PAID_CODE = "boileruphammerdown";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function entry(entries, key) {
  return entries.find((item) => item.key === key);
}

async function installScanRecorder(page) {
  await page.evaluate(() => {
    window.__scanSubmissions = [];
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const url = typeof input === "string" ? input : input?.url || "";
      if (url.includes("/api/scan") && String(init.method || "GET").toUpperCase() === "POST") {
        const entries = [];
        const body = init.body;
        if (body && typeof body.entries === "function") {
          for (const [key, value] of body.entries()) {
            if (value instanceof File) {
              entries.push({
                key,
                name: value.name,
                type: value.type,
                size: value.size,
              });
            } else {
              entries.push({ key, value: String(value) });
            }
          }
        }
        window.__scanSubmissions.push(entries);
        return new Response(JSON.stringify({
          ok: true,
          scan_id: "e2efunded001",
          status: "queued",
          proxied: true,
          analysis_mode: "tribe_video",
          compute_target: entries.find((item) => item.key === "compute_target")?.value || "local",
        }), {
          status: 202,
          headers: { "content-type": "application/json" },
        });
      }
      return realFetch(input, init);
    };
  });
}

async function waitForSubmission(page, index) {
  await page.waitForFunction((i) => window.__scanSubmissions?.length > i, index, {
    timeout: 10000,
  });
  return page.evaluate((i) => window.__scanSubmissions[i], index);
}

async function run() {
  assert(existsSync(CHROME_PATH), `Chrome executable not found at ${CHROME_PATH}`);

  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROME_PATH,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.setDefaultTimeout(20000);
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message || String(error)));

  await page.goto(`${BASE_URL}/?fundedE2E=${Date.now()}`, { waitUntil: "domcontentloaded" });
  await installScanRecorder(page);

  const cloudRadio = page.locator('input[name="compute-target"][value="cloud_hf"]');
  await cloudRadio.waitFor();
  assert(await cloudRadio.isDisabled(), "cloud compute should be disabled before funded unlock");
  assert(await page.locator("#cloud-compute-option").evaluate((el) => el.classList.contains("locked")), "cloud option should render locked before unlock");

  const legacySelect = page.locator("#narration-model-select");
  assert(!(await legacySelect.isVisible()), "legacy narration select should stay hidden behind the card catalog");
  await page.waitForFunction(() => {
    const cards = Array.from(document.querySelectorAll('#model-card-list [data-model]'));
    const selectable = cards.filter((card) => card.dataset.locked !== "true");
    const locked = cards.filter((card) => card.dataset.locked === "true");
    return selectable.length > 0 && locked.length > 0 && selectable.every((card) => {
      const model = card.dataset.model || "";
      return model.endsWith(":free") || model === "openrouter:openrouter/free";
    }) && locked.every((card) => !(card.dataset.model || "").endsWith(":free"));
  });
  const visibleSections = await page.locator("#model-card-list .model-section-header").allTextContents();
  assert(visibleSections.some((text) => text.includes("Free OpenRouter")), "free section header should be visible");
  assert(visibleSections.some((text) => text.includes("Funded OpenRouter")), "funded locked section header should be visible");
  const lockedPaidCopy = await page.locator('#model-card-list [data-locked="true"] small').first().textContent();
  assert((lockedPaidCopy || "").includes("Please fund Red Team Kitchen"), "paid cards should ask for funding before unlock");

  const rightRailAudit = await page.evaluate(() => ({
    resetButtons: document.querySelectorAll("#reset-layout-btn").length,
    gridItems: document.querySelectorAll(".grid-stack,.grid-stack-item").length,
    collapsibleCards: document.querySelectorAll("details.cd-card").length,
    rightText: document.querySelector(".panel-right")?.innerText || "",
    sourceEvidenceExists: !!document.querySelector("#source-evidence"),
    sourceEvidenceInitiallyHidden: document.querySelector("#source-evidence")?.hidden === true,
  }));
  assert(rightRailAudit.resetButtons === 0, "right rail should not inject a reset layout button");
  assert(rightRailAudit.gridItems === 0, "right rail should not be wrapped in GridStack placeholders");
  assert(rightRailAudit.collapsibleCards === 0, "right rail should not hide results inside collapsible cards");
  assert(rightRailAudit.sourceEvidenceExists, "right rail should include a source evidence panel");
  assert(rightRailAudit.sourceEvidenceInitiallyHidden, "source evidence should stay hidden until media context exists");
  const rightText = rightRailAudit.rightText.toLowerCase();
  assert(rightText.includes("current analysis shape"), "right rail should show current analysis context");
  assert(rightText.includes("persona narrations"), "right rail should expose persona narrations");
  assert(rightText.includes("network summary"), "right rail should expose the network summary tab");

  await page.locator("#paid-access-code").fill(PAID_CODE);
  await page.locator("#paid-access-btn").click();
  await page.locator(".purdue-mark").waitFor({ state: "visible" });
  assert(await page.locator(".purdue-mark").textContent() === "P", "Purdue mark should appear after unlock");
  assert(!(await cloudRadio.isDisabled()), "cloud compute should be enabled after funded unlock");

  await page.waitForFunction(() => {
    const paidCards = Array.from(document.querySelectorAll('#model-card-list [data-group="paid"]'));
    return paidCards.length > 0 && paidCards.every((card) => card.dataset.locked !== "true");
  });
  const paidCard = page.locator('#model-card-list [data-group="paid"]').first();
  const paidModel = await paidCard.getAttribute("data-model");
  assert(paidModel && !paidModel.endsWith(":free"), "funded model should be a paid OpenRouter slug");
  await paidCard.click();

  await cloudRadio.check({ force: true });
  await page.locator("#scan-file").setInputFiles({
    name: "funded-cloud-demo.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("fake funded video bytes"),
  });
  await page.locator("#scan-submit").click();
  const submission = await waitForSubmission(page, 0);

  const file = entry(submission, "file");
  assert(file?.name === "funded-cloud-demo.mp4", "funded upload should submit the selected video");
  assert(file?.type === "video/mp4", "funded upload should preserve video mime type");
  assert(file?.size > 10, "funded upload file should not be empty");
  assert(entry(submission, "narration_model")?.value === paidModel, "funded upload should submit selected paid model");
  assert(entry(submission, "compute_target")?.value === "cloud_hf", "funded upload should submit cloud_hf target");
  assert(entry(submission, "paid_access_code")?.value === PAID_CODE, "funded upload should submit access code");

  await page.waitForFunction(() => typeof window.cortexCharts?.renderNetworkSummary === "function");
  await page.evaluate(() => {
    window.lastScanResult = {
      top_rois: [
        "7Networks_RH_Vis_4",
        "7Networks_LH_Vis_15",
        "7Networks_RH_Default_2",
        "7Networks_LH_SomMot_3",
      ],
      top_roi_z: [4.5, 3.9, 2.1, 1.6],
      peak_t: 11,
      n_t: 64,
    };
    window.cortexCharts.activateTab("networks");
  });
  await page.locator("#chart-networks svg").waitFor({ state: "attached" });
  const networkSvg = await page.locator("#chart-networks svg").count();
  assert(networkSvg > 0, "network summary should attach an svg");
  const networkBars = await page.locator("#chart-networks .network-bar").count();
  assert(networkBars > 0, "network summary should render ranked bars");
  assert(pageErrors.length === 0, `page should not throw JS errors: ${pageErrors.join(" | ")}`);

  await browser.close();
  console.log(JSON.stringify({
    ok: true,
    paidModel,
    computeTarget: entry(submission, "compute_target")?.value,
    paidAccess: entry(submission, "paid_access_code")?.value === PAID_CODE,
    file: {
      name: file.name,
      type: file.type,
      size: file.size,
    },
  }, null, 2));
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
