// Run against the disposable browser app in tests/browser/serve.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use a disposable loopback test app.');

(async () => {
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-lr-'));
    fs.mkdirSync(artifacts, {recursive: true});
    const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], failures = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {if (response.status() >= 500) failures.push(response.url());});
    try {
        if (process.env.SYSTEM_TEST_PASSWORD) {
            await page.goto(base + '/admin/login');
            await page.locator('[name=email]').fill('admin@example.test');
            await page.locator('[name=password]').fill(process.env.SYSTEM_TEST_PASSWORD);
            await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        }
        await page.goto(base + '/admin/lr-generator');
        await page.evaluate(() => sessionStorage.clear()); await page.reload();
        await page.locator('[name=lr_shipment]').first().check();
        await page.getByRole('link', {name: 'Next', exact: true}).click();
        assert.equal(await page.locator('#lr-selected-count').textContent(), '1');
        await page.locator('[name=lr_shipment]').first().check();
        assert.equal(await page.locator('#lr-selected-count').textContent(), '2');
        await page.reload();
        assert.equal(await page.locator('#lr-selected-list li').count(), 2);
        const search = await page.locator('#lr-selected-list li span').first().textContent();
        await page.locator('#lr-search').fill(search);
        await page.getByRole('button', {name: 'Search', exact: true}).click();
        await page.waitForFunction(() => document.querySelector('#lr-selected-count').textContent === '2');
        assert.equal(await page.locator('#lr-selected-count').textContent(), '2');
        await page.locator('#lr-review').click();
        const modal = page.locator('#lrReviewModal'); await modal.waitFor({state: 'visible'});
        assert.equal(await page.locator('#lr-review-rows > section').count(), 2);
        assert.ok(await modal.getByText('From shipment · review before generating').count() > 0);
        await page.locator('#lr-download').click();
        assert.ok(await page.locator('#lr-details-form :invalid').count() > 0);
        const cards = page.locator('#lr-review-rows > section');
        for (let index = 0; index < 2; index++) {
            const card = cards.nth(index);
            const values = {vehicle_number: 'MH12AB3456', vehicle_type: '32 Feet SXL',
                consignee_name: 'Receiving Company', goods_description: 'Packaged consumer goods',
                chargeable_weight: '150', cargo_value: '12500.50', invoice_number: 'INV-' + index,
                invoice_date: '2026-10-19', invoice_value: '12500.50'};
            for (const [name, value] of Object.entries(values)) await card.locator('[data-field=' + name + ']').fill(value);
        }
        await modal.getByRole('button', {name: 'Back', exact: true}).click(); await modal.waitFor({state: 'hidden'});
        await page.reload(); await page.locator('#lr-review').click(); await modal.waitFor({state: 'visible'});
        assert.equal(await page.locator('[data-field=invoice_number]').first().inputValue(), 'INV-0');
        const download = page.waitForEvent('download'); await page.locator('#lr-download').click();
        await (await download).saveAs(path.join(artifacts, 'lorry-receipts.pdf'));
        await page.waitForFunction(() => !document.querySelector('#lr-download').disabled);
        const popup = page.waitForEvent('popup'); await page.locator('#lr-preview').click();
        const preview = await popup;
        await page.waitForFunction(() => !document.querySelector('#lr-download').disabled);
        await preview.waitForURL('blob:**'); await preview.close();
        for (const [theme, width, height] of [['light', 1440, 1000], ['dark', 1440, 1000], ['dark', 390, 844], ['light', 768, 1024]]) {
            await page.setViewportSize({width, height});
            await page.evaluate(theme => document.documentElement.setAttribute('data-bs-theme', theme), theme);
            await page.waitForTimeout(150);
            const box = await page.locator('#lr-download').boundingBox();
            assert.ok(box.x >= 0 && box.x + box.width <= width && box.y + box.height <= height);
            await page.screenshot({path: path.join(artifacts, 'lr-' + theme + '-' + width + '.png')});
        }
        assert.deepEqual(errors, []); assert.deepEqual(failures, []);
        console.log('PASS: cross-page selection, search, draft restoration, required form, download, preview and responsive themes');
        console.log('Artifacts: ' + artifacts);
    } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
