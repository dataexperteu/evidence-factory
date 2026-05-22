const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const results = [];

  function log(icon, label, detail) {
    const line = `${icon} ${label}${detail ? ' → ' + detail : ''}`;
    results.push(line);
    console.log(line);
  }

  // --- Test 1: panel visible on fresh load ---
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('http://localhost:5173/');
    await page.waitForLoadState('networkidle');

    const panel = page.locator('[data-testid="help-panel"]');
    const visible = await panel.isVisible();
    log(visible ? '✅' : '❌', 'Help panel visible on fresh load', `visible=${visible}`);

    // Screenshot of initial state
    await page.screenshot({ path: '/tmp/ss1-initial.png' });
    log('📸', 'Screenshot saved', '/tmp/ss1-initial.png');

    // Check CSS class
    const cls = await panel.getAttribute('class');
    const hasClass = cls && cls.includes('help-banner');
    log(hasClass ? '✅' : '❌', 'Panel has .help-banner CSS class', `class="${cls}"`);

    // Check aria-label on dismiss button
    const btn = page.locator('[data-testid="help-dismiss"]');
    const aria = await btn.getAttribute('aria-label');
    log(aria === 'Dismiss help' ? '✅' : '❌', 'Dismiss button aria-label', `"${aria}"`);

    // Check content text
    const content = page.locator('.help-content');
    const text = await content.innerText();
    const hasCorpus = text.includes('synthetic forensic evidence corpus');
    const hasDifficulty = text.includes('difficulty preset');
    const hasZip = text.includes('corpus.zip');
    const hasManifest = text.includes('MANIFEST.txt');
    log(hasCorpus && hasDifficulty && hasZip && hasManifest ? '✅' : '❌',
      'Help content has required text',
      `corpus=${hasCorpus} difficulty=${hasDifficulty} zip=${hasZip} manifest=${hasManifest}`);

    await ctx.close();
  }

  // --- Test 2: clicking × dismisses panel ---
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('http://localhost:5173/');
    await page.waitForLoadState('networkidle');

    const btn = page.locator('[data-testid="help-dismiss"]');
    await btn.click();
    await page.waitForTimeout(300);

    const panel = page.locator('[data-testid="help-panel"]');
    const visible = await panel.isVisible();
    log(!visible ? '✅' : '❌', 'Panel hidden after clicking ×', `still_visible=${visible}`);

    await page.screenshot({ path: '/tmp/ss2-dismissed.png' });
    log('📸', 'Screenshot after dismiss', '/tmp/ss2-dismissed.png');

    // Form still accessible
    const generate = page.locator('[data-testid="generate"]');
    const genVisible = await generate.isVisible();
    log(genVisible ? '✅' : '❌', 'Generate button still visible after dismiss', `visible=${genVisible}`);

    await ctx.close();
  }

  // --- Test 3: dismissed state persists on reload ---
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('http://localhost:5173/');
    await page.waitForLoadState('networkidle');

    // Dismiss
    await page.locator('[data-testid="help-dismiss"]').click();
    await page.waitForTimeout(300);

    // Reload
    await page.reload();
    await page.waitForLoadState('networkidle');

    const panel = page.locator('[data-testid="help-panel"]');
    const visible = await panel.isVisible();
    log(!visible ? '✅' : '❌', 'Panel stays hidden after reload (localStorage)', `visible=${visible}`);

    // Verify localStorage flag
    const flag = await page.evaluate(() => localStorage.getItem('help-dismissed'));
    log(flag === 'true' ? '✅' : '❌', 'localStorage "help-dismissed" set to "true"', `flag="${flag}"`);

    await ctx.close();
  }

  // --- Test 4 (probe): panel reappears after localStorage cleared ---
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('http://localhost:5173/');
    await page.waitForLoadState('networkidle');

    // Dismiss and reload to confirm it's gone
    await page.locator('[data-testid="help-dismiss"]').click();
    await page.waitForTimeout(200);
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Clear flag
    await page.evaluate(() => localStorage.removeItem('help-dismissed'));
    await page.reload();
    await page.waitForLoadState('networkidle');

    const panel = page.locator('[data-testid="help-panel"]');
    const visible = await panel.isVisible();
    log(visible ? '🔍✅' : '🔍❌', 'Panel reappears after localStorage cleared', `visible=${visible}`);

    await ctx.close();
  }

  // --- Probe: dismiss × button hover doesn't break anything ---
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('http://localhost:5173/');
    await page.waitForLoadState('networkidle');

    const btn = page.locator('[data-testid="help-dismiss"]');
    await btn.hover();
    await page.waitForTimeout(200);
    const panel = page.locator('[data-testid="help-panel"]');
    const stillVisible = await panel.isVisible();
    log(stillVisible ? '🔍✅' : '🔍❌', 'Panel still visible after hovering × (no accidental dismiss on hover)', `visible=${stillVisible}`);

    await ctx.close();
  }

  await browser.close();

  const failed = results.filter(r => r.includes('❌')).length;
  console.log(`\n${failed === 0 ? 'ALL PASS' : `FAIL: ${failed} check(s) failed`}`);
  process.exit(failed > 0 ? 1 : 0);
})();
