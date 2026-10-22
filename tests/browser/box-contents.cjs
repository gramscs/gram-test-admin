// Only use the disposable app from serve.py. Never run against business data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {execFileSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Use a disposable loopback app.');
(async () => {
    const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}}), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const prefix = 'BOX-UI-' + Date.now();
    const directory = process.env.SYSTEM_TEST_ARTIFACTS_DIR || '/tmp/gram-box-browser-artifacts';
    fs.mkdirSync(directory, {recursive: true});
    let row;
    try {
        await page.goto(base + '/admin/login');
        await page.locator('[name=email]').fill('admin@example.test');
        await page.locator('[name=password]').fill(process.env.SYSTEM_TEST_PASSWORD);
        await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        await page.goto(base + '/admin/shipments');
        await page.locator('#add-row-btn').click();
        await page.locator('#modal-consignment-number').fill(prefix);
        await page.locator('#modal-status').selectOption('In Transit');
        await page.locator('#modal-pieces').fill('100');
        await page.locator('#modal-weight').fill('18.500');
        await page.locator('#modal-measurement-mode').selectOption('volume');
        await page.locator('#modal-volume').fill('250');
        await page.locator('#modal-pickup-address').fill('GRAM warehouse, Delhi 110001');
        await page.locator('#modal-drop-address').fill('Receiving warehouse, Mumbai 400001');
        await page.locator('#modal-pickup-date').fill('2026-10-13');
        await page.locator('#edit-box-contents').click();
        await page.locator('#package-default').fill('12');
        assert.equal(await page.locator('#package-contents-editor input').count(), 1);
        assert.equal(await page.locator('#package-contents-editor table').count(), 0);
        await page.screenshot({path: directory + '/box-contents-desktop.png'});
        // Invalid shared counts must stay visible inside the open modal.
        await page.locator('#package-default').fill('-1');
        await page.locator('#modal-save-btn').click();
        await page.locator('#shipment-form-error:not(.d-none)').waitFor();
        assert.match(await page.locator('#shipment-form-error').textContent(), /Pieces per box/);
        await page.locator('#package-default').fill('12');
        await page.locator('#modal-save-btn').click();
        await page.locator('#editConsignmentModal').waitFor({state: 'hidden'});
        const saved = page.waitForResponse(r => r.url().endsWith('/consignments/save'));
        await page.locator('#save-btn').click(); assert.equal((await saved).status(), 200);
        row = (await (await page.request.get(base + '/admin/consignments/list?search=' + prefix)).json()).rows[0];
        assert.deepEqual(row.package_contents, {kind: 'box', default_pieces: 12});
        assert.equal(row.volume_unit, 'ft3'); assert.equal(row.volume, '250.000');
        console.log('PASS: 100 boxes, shared contents, modal validation and SQL persistence.');
        await page.reload(); await page.locator('#search-input').fill(prefix);
        await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
        await page.locator('.edit-row').click(); await page.locator('#edit-box-contents').click();
        assert.equal(await page.locator('#package-default').inputValue(), '12');
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: directory + '/box-contents-mobile.png'});
        await page.locator('#back-to-shipment').click(); await page.locator('#modal-pieces').fill('50');
        await page.locator('#edit-box-contents').click();
        assert.match(await page.locator('.package-summary').textContent(), /all 50 boxes/);
        await page.locator('#package-kind').selectOption('pallet');
        await page.locator('#modal-save-btn').click();
        await page.locator('#editConsignmentModal').waitFor({state: 'hidden'});
        const resized = page.waitForResponse(r => r.url().endsWith('/consignments/save'));
        await page.locator('#save-btn').click(); assert.equal((await resized).status(), 200);
        const reduced = (await (await page.request.get(base + '/admin/consignments/list?search=' + prefix)).json()).rows[0];
        assert.equal(reduced.pieces, 50);
        assert.deepEqual(reduced.package_contents, {kind: 'pallet', default_pieces: 12});
        // Restore 100 boxes for the batch-label test through the normal save endpoint.
        assert.equal((await page.request.post(base + '/admin/consignments/save', {data: {rows: [row]}})).status(), 200);
        console.log('PASS: reload, mobile controls, package type and reducing box count.');
        await page.setViewportSize({width: 1440, height: 1000});
        await page.goto(base + '/admin/labels?search=' + prefix);
        await page.locator('#select-all-labels').check(); await page.locator('#review-labels-btn').click();
        assert.match(await page.locator('.label-package-summary').textContent(), /12 pieces per box/);
        assert.equal(await page.locator('[data-box-invoice]').count(), 0);
        assert.equal(await page.locator('[data-field=invoice_amount]').count(), 1);
        await page.locator('[data-field=invoice_amount]').fill('6000');
        await page.locator('[data-field=goods_description]').fill('Precision components');
        await page.screenshot({path: directory + '/label-package-review.png'});
        const download = page.waitForEvent('download'); await page.locator('#download-labels-btn').click();
        await (await download).saveAs(directory + '/box-labels.pdf');
        const text = execFileSync('pdftotext', [directory + '/box-labels.pdf', '-'], {encoding: 'utf8'});
        const pages = text.split('\f').filter(t => t.trim()); assert.equal(pages.length, 100);
        for (let i = 0; i < 100; i++) {
            assert(pages[i].includes((i + 1) + ' of 100'));
            assert(pages[i].includes('250.000 ft³'));
            assert(pages[i].includes('12'));
            assert(pages[i].includes('Rs 6,000.00'));
            assert(!pages[i].includes('kg'));
            assert(!pages[i].includes('BOX NOTE'));
        }
        assert.deepEqual(errors, []);
        console.log('PASS: one invoice field per shipment; all 100 PDF labels use shared contents, volume and invoice amount with sequential box numbers.');
    } finally {
        if (row) await page.request.post(base + '/admin/consignments/save', {data: {rows: [], deleted_ids: [row.id]}});
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
