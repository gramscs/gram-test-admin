// Use only the disposable migrated app from tests/browser/serve.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use a disposable loopback app and its test password.');

(async () => {
    const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [], failures = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {if (response.status() >= 500) failures.push(response.url());});
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-label-review-'));
    fs.mkdirSync(artifacts, {recursive: true});
    const prefix = 'LABEL-UI-' + Date.now();
    let ids = [];
    const modal = page.locator('#labelReviewModal');
    try {
        await page.goto(base + '/admin/login');
        await page.locator('[name=email]').fill('admin@example.test');
        await page.locator('[name=password]').fill(process.env.SYSTEM_TEST_PASSWORD);
        await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        for (const route of ['/admin/dashboard', '/admin/consignments', '/admin/mis', '/admin/labels']) {
            await page.goto(base + route);
            assert.ok(!/chargeable/i.test(await page.locator('body').innerText()), route);
            assert.equal(await page.locator('[aria-label*=chargeable i]').count(), 0);
        }
        const saved = await page.request.post(base + '/admin/consignments/save', {data: {rows: Array.from({length: 50}, (_, i) => ({consignment_number: prefix + '-' + String(i).padStart(2, '0'), identifier_type: 'LRN', pieces: i === 0 ? 2 : 1, weight: '100', pickup_date: '2026-10-09', pickup_address: '429 Jagraj Gram, Delhi', drop_address: 'Ms Ashiya, Delhi', drop_pincode: '110037'}))}});
        assert.equal(saved.status(), 200);
        const baseline = (await (await page.request.get(base + '/admin/consignments/list', {params: {search: prefix, per_page: 100}})).json()).rows;
        ids = baseline.map(row => row.id);
        await page.goto(base + '/admin/labels?' + new URLSearchParams({search: prefix}));
        await page.locator('#select-all-labels').check();
        assert.equal(await page.locator('#label-selected-count').textContent(), '25');
        await page.getByRole('link', {name: 'Next', exact: true}).click();
        await page.locator('#select-all-labels').check();
        assert.equal(await page.locator('#label-selected-count').textContent(), '50');
        assert.equal(await page.locator('#label-piece-count').textContent(), '51');
        await page.reload(); assert.equal(await page.locator('#label-selected-count').textContent(), '50');
        await page.locator('#review-labels-btn').click(); await modal.waitFor({state: 'visible'});
        assert.equal(await page.locator('.label-review-card').count(), 50);
        assert.match(await page.locator('#label-review-summary').textContent(), /50 shipments.*51 box\/pallet labels/);
        console.log('PASS: 50 shipments selected across pages, refresh preserved, one review card per shipment');

        await page.locator('#label-common-payment').selectOption('to-pay');
        await page.locator('#label-common-description').fill('Automotive spare parts');
        await page.locator('#apply-label-common').click();
        assert.equal(await page.locator('[data-field=payment_mode]').first().inputValue(), 'to-pay');
        await page.locator('#download-labels-btn').click();
        await page.locator('#label-review-error:not(.d-none)').waitFor();
        assert.match(await page.locator('#label-review-error').textContent(), /Complete the details/);
        await page.locator('[data-field=invoice_amount]').first().fill('6000.50');
        await page.evaluate(() => {
            document.querySelectorAll('[data-field=invoice_amount]').forEach((input, i) => {
                if (i > 0) {input.value = String(6000 + i); input.dispatchEvent(new Event('input', {bubbles: true}));}
            });
        });
        const first = page.locator('.label-review-card').first();
        assert.equal(await first.locator('[data-field=sender_name]').inputValue(), '');
        assert.equal(await first.locator('[data-field=receiver_name]').inputValue(), '');
        assert.equal(await first.locator('[data-field=sender_name]').evaluate(input => input.required), false);
        await first.locator('[data-field=sender_name]').fill('GRAM Dispatch Team');
        await first.locator('[data-field=receiver_name]').fill('Ashiya Receiving Team');
        await first.locator('[data-field=eway_bill_exists]').check();
        await first.locator('[data-field=eway_bill_number]').fill('123456789012');
        const last = page.locator('.label-review-card').last();
        await last.locator('[data-field=payment_mode]').selectOption('cash-on-delivery');
        await last.locator('[data-field=goods_description]').fill('Tyres & wheels <boxed>');
        assert.equal(await page.locator('#label-review-ready').textContent(), '50 / 50 ready');
        console.log('PASS: common defaults, individual overrides, invoice amount validation and optional e-way bill');

        const submitted = page.waitForRequest(request => request.url().endsWith('/labels/generate'));
        const download = page.waitForEvent('download');
        await page.locator('#download-labels-btn').click();
        const payload = (await submitted).postDataJSON();
        assert.equal(payload.label_details[0].sender_name, 'GRAM Dispatch Team');
        assert.equal(payload.label_details[0].receiver_name, 'Ashiya Receiving Team');
        assert.equal(payload.label_details.at(-1).sender_name, '');
        await (await download).saveAs(path.join(artifacts, 'labels-50-shipments.pdf'));
        await page.waitForFunction(() => !document.querySelector('#download-labels-btn').disabled);
        const run = await page.evaluate(() => JSON.parse(sessionStorage.getItem('gram-label-selection:admin')).job);
        assert.ok(run);
        const preview = page.waitForEvent('popup');
        await page.locator('#preview-labels-btn').click();
        const previewPage = await preview;
        await page.waitForFunction(() => !document.querySelector('#download-labels-btn').disabled);
        assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('gram-label-selection:admin')).job), run);
        await previewPage.close();
        await modal.getByRole('button', {name: 'Back', exact: true}).click(); await modal.waitFor({state: 'hidden'});
        await page.reload(); await page.locator('#review-labels-btn').click(); await modal.waitFor({state: 'visible'});
        assert.equal(await page.locator('#label-review-ready').textContent(), '50 / 50 ready');
        assert.equal(await page.locator('[data-field=sender_name]').first().inputValue(), 'GRAM Dispatch Team');
        assert.equal(await page.locator('[data-field=receiver_name]').first().inputValue(), 'Ashiya Receiving Team');
        assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('gram-label-selection:admin')).job), run);
        const again = page.waitForEvent('download'); await page.locator('#download-labels-btn').click();
        await (await again).saveAs(path.join(artifacts, 'labels-reprint.pdf'));
        await page.waitForFunction(() => !document.querySelector('#download-labels-btn').disabled);
        const after = (await (await page.request.get(base + '/admin/consignments/list', {params: {search: prefix, per_page: 100}})).json()).rows;
        assert.deepEqual(after, baseline);
        console.log('PASS: real PDF download, preview and reprint reuse batch numbers; shipment data unchanged');

        for (const [theme, width, height] of [['light', 1440, 1000], ['dark', 1440, 1000], ['dark', 390, 844], ['light', 768, 1024]]) {
            await page.setViewportSize({width, height});
            await page.evaluate(theme => document.documentElement.setAttribute('data-bs-theme', theme), theme);
            await page.waitForTimeout(150);
            const reachable = await page.locator('#download-labels-btn').evaluate(button => {
                const rect = button.getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth;
            });
            assert.equal(reachable, true, theme + ' ' + width);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            assert.equal(await page.locator('[data-field=sender_name]').first().evaluate(input => input.getBoundingClientRect().right <= innerWidth), true);
            await page.screenshot({path: path.join(artifacts, 'review-' + theme + '-' + width + '.png')});
        }
        assert.deepEqual(errors, []); assert.deepEqual(failures, []);
        console.log('PASS: light/dark and desktop/mobile/tablet review footer stays reachable; no JS or server errors');
        console.log('Artifacts: ' + artifacts);
    } finally {
        if (ids.length) await page.request.post(base + '/admin/consignments/save', {data: {rows: [], deleted_ids: ids}});
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
