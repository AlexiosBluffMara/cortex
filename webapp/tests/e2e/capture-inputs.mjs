import { chromium } from "playwright-core";
import { existsSync } from "node:fs";

const BASE_URL = process.env.CORTEX_E2E_BASE_URL || "http://127.0.0.1:8765";
const DEFAULT_CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const CHROME_PATH = process.env.CHROME_PATH || DEFAULT_CHROME;

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function entry(entries, key) {
  return entries.find((item) => item.key === key);
}

async function installFetchRecorder(page) {
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
        const file = entries.find((item) => item.key === "file");
        const isAudio = file?.type?.startsWith("audio/");
        return new Response(JSON.stringify({
          ok: true,
          scan_id: isAudio ? "e2evoice0001" : "e2ecamera001",
          status: "queued",
          analysis_mode: isAudio ? "tribe_audio" : "tribe_text_bridge_image",
          compute_target: entry(entries, "compute_target")?.value || "local",
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
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      "--allow-file-access-from-files",
    ],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 960 },
    permissions: ["camera", "microphone"],
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);

  await page.goto(`${BASE_URL}/?captureE2E=${Date.now()}`, { waitUntil: "domcontentloaded" });
  await installFetchRecorder(page);

  await page.locator(".capture-drawer summary").click();

  await page.locator('[data-mode="camera"]').click();
  await page.locator("#camera-open-btn").click();
  await page.locator("#camera-capture-btn").waitFor({ state: "visible" });
  await page.locator("#camera-capture-btn").click();
  await page.locator("#camera-submit-btn").waitFor({ state: "visible" });
  await page.locator("#camera-submit-btn").click();
  const cameraSubmission = await waitForSubmission(page, 0);
  const cameraFile = entry(cameraSubmission, "file");
  assert(cameraFile?.name === "capture.jpg", "camera should submit capture.jpg");
  assert(cameraFile?.type === "image/jpeg", "camera should submit image/jpeg");
  assert(cameraFile?.size > 100, "camera capture should not be empty");
  assert(entry(cameraSubmission, "narration_model")?.value?.endsWith(":free"), "camera should use a free narration model by default");
  assert(entry(cameraSubmission, "compute_target")?.value === "local", "camera should default to local compute");
  assert(entry(cameraSubmission, "paid_access_code")?.value === "", "camera should not send funded access by default");

  await page.locator('[data-mode="voice"]').click();
  await page.locator("#voice-record-btn").click();
  await page.waitForTimeout(1200);
  await page.locator("#voice-stop-btn").click();
  await page.locator("#voice-submit-btn").waitFor({ state: "visible" });
  await page.locator("#voice-submit-btn").click();
  const voiceSubmission = await waitForSubmission(page, 1);
  const voiceFile = entry(voiceSubmission, "file");
  assert(voiceFile?.name?.startsWith("recording."), "voice should submit a recording file");
  assert(voiceFile?.type?.startsWith("audio/"), `voice should submit audio/*, got ${voiceFile?.type}`);
  assert(voiceFile?.size > 100, "voice recording should not be empty");
  assert(entry(voiceSubmission, "compute_target")?.value === "local", "voice should default to local compute");

  await browser.close();
  console.log(JSON.stringify({
    ok: true,
    camera: {
      name: cameraFile.name,
      type: cameraFile.type,
      size: cameraFile.size,
    },
    voice: {
      name: voiceFile.name,
      type: voiceFile.type,
      size: voiceFile.size,
    },
  }, null, 2));
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});

