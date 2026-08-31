// Records cuts 1-6 (the film flow) as one continuous take.
// Real screen, real production URL, real timings - nothing is faked.
// Waiting/scrolling paces itself for a human narrator; ffmpeg speeds up the
// dead air (upload/analysis waits) in the edit pass, same principle as the
// storyboard's own note: "待ち時間は早送りし、見せ場は等速で見せる。"
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

  console.log('cut1: opening the app, empty state');
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'EN' }).click();
  await pause(1000);
  // footer with the catalogue counts should be in view for a beat
  await page.locator('text=/2,719 films/').scrollIntoViewIfNeeded();
  await pause(2500);
  await page.locator('text=/2,719 films/').scrollIntoViewIfNeeded({ block: 'start' });
  await page.evaluate(() => window.scrollTo(0, 0));
  await pause(1500);

  console.log('cut2: loading the sample, running the committee');
  await page.getByRole('button', { name: 'The Solitary Orbit' }).click();
  await pause(1500);
  // confirm the fields are visibly populated before running
  await pause(1000);
  await page.getByRole('button', { name: 'Run the committee' }).click();
  console.log('  waiting for analysis to complete...');
  await page.getByRole('heading', { name: 'What the agents did' }).waitFor({ timeout: 60000 });
  console.log('  analysis complete');

  console.log('cut3: scrolling to the agent trace, pausing on the SQL block');
  await page.getByRole('heading', { name: 'What the agents did' }).scrollIntoViewIfNeeded();
  await pause(2000);
  // the "find_comparable_titles" SQL block - the one the narration calls out
  const sqlBlocks = page.locator('text=SQL the analyst wrote');
  await sqlBlocks.first().scrollIntoViewIfNeeded();
  await pause(4000);

  console.log('cut4: scrolling to the memo');
  await page.getByRole('heading', { name: 'The memo' }).scrollIntoViewIfNeeded();
  await pause(2000);
  await page.locator('text=WHERE THIS COULD BE WRONG').scrollIntoViewIfNeeded();
  await pause(3000);

  console.log('cut5: back up to comparable titles, unchecking the weakest one');
  await page.getByRole('heading', { name: /Comparable titles/ }).scrollIntoViewIfNeeded();
  await pause(1500);
  // uncheck the lowest-ROI title - "that title is not comparable"
  await page.getByRole('checkbox', { name: 'Sea Fever' }).uncheck();
  await pause(1000);
  await page.locator('text=What the score is made of').scrollIntoViewIfNeeded();
  await pause(3000);

  console.log('cut6: the verdict ladder');
  await page.getByRole('heading', { name: 'Move a lever' }).scrollIntoViewIfNeeded();
  await pause(4000);

  await pause(1000);
  await context.close();
  await browser.close();

  const files = fs.readdirSync(OUT_DIR).filter((f) => f.endsWith('.webm'));
  console.log('saved:', files);
};

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
