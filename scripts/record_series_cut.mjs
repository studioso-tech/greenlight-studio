// Records cut 7 (drama and Japanese) as one continuous take.
import { chromium } from 'playwright';
import fs from 'node:fs';

const URL = 'https://greenlight-studio-667011739762.us-central1.run.app';
const OUT_DIR = 'media/demo_takes_raw';
fs.mkdirSync(OUT_DIR, { recursive: true });

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

const run = async () => {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    recordVideo: { dir: OUT_DIR, size: { width: 1920, height: 1080 } },
  });
  const page = await context.newPage();

  console.log('setup: series mode, EN, sample loaded, run');
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'EN' }).click();
  await pause(500);
  await page.getByRole('button', { name: 'Series' }).click();
  await pause(500);
  await page.getByRole('button', { name: 'Late Bloom' }).click();
  await pause(1000);
  await page.getByRole('button', { name: 'Run the committee' }).click();
  console.log('  waiting for analysis to complete...');
  await page.getByRole('heading', { name: 'What the agents did' }).waitFor({ timeout: 60000 });
  console.log('  analysis complete - this is the "already run" state the cut records from');

  console.log('cut7: showing the series-specific metrics');
  await page.evaluate(() => window.scrollTo(0, 0));
  await pause(1000);
  await page.locator('text=Returns for season 2').scrollIntoViewIfNeeded();
  await pause(3000);

  console.log('cut7: toggling to Japanese');
  await page.getByRole('button', { name: '日本語' }).click();
  await pause(1000);
  await page.evaluate(() => window.scrollTo(0, 0));
  await pause(3500);

  await context.close();
  await browser.close();

  const files = fs.readdirSync(OUT_DIR).filter((f) => f.endsWith('.webm'));
  console.log('saved:', files);
};

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
