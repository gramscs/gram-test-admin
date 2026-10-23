// Run only against tests/browser/serve.py's disposable local app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use the disposable local app and test password.');

(async () => {
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-admin-followup-'));
    fs.mkdirSync(artifacts, {recursive: true});
    const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const prefix = 'FOLLOWUP-' + Date.now();
    let ids = [];
    try {
        await page.goto(base + '/admin/login');
        assert.equal(await page.getByRole('link', {name: 'Admin Home', exact: true}).count(), 0);
        await page.locator('#email').fill('admin@example.test'); await page.locator('#password').fill(process.env.SYSTEM_TEST_PASSWORD);
        await page.getByRole('button', {name: /Sign In/}).click(); await page.waitForURL('**/admin/dashboard');
        const created = await page.request.post(base + '/admin/companies', {data: {name: prefix, address: 'Registered office', email: 'client@example.test', phone: '1234567890', locations: [
            {kind: 'pickup', label: 'Warehouse', address: 'Pickup address', pincode: '110001', is_default: true},
            {kind: 'drop', label: 'Office', address: 'Drop address', pincode: '400001', is_default: true}]}});
        assert.equal(created.status(), 200); const company = (await created.json()).company;
        await page.goto(base + '/admin/companies?' + new URLSearchParams({search: prefix}));
        let writes = 0; page.on('request', request => {if (request.method() !== 'GET' && request.url().includes('/admin/companies')) writes++;});
        const modal = page.locator('#companyModal');
        await page.getByRole('button', {name: 'View ' + prefix, exact: true}).click(); await modal.waitFor({state: 'visible'});
        assert.equal(await page.locator('#company-modal-title').textContent(), 'View Company');
        assert.equal(await page.locator('#company-name').inputValue(), prefix);
        assert.equal(await modal.locator('textarea.location-address').count(), 2);
        assert.equal(await page.locator('#company-form').evaluate(form => Array.from(form.querySelectorAll('input,textarea')).every(input => input.type === 'checkbox' ? input.disabled : input.readOnly)), true);
        assert.equal(await page.locator('#company-save-btn').isVisible(), false);
        assert.equal(await modal.locator('.add-location:visible,.remove-location:visible').count(), 0);
        await page.locator('#company-name').press('Enter'); assert.equal(writes, 0);
        await page.screenshot({path: path.join(artifacts, 'company-view-desktop.png')});
        await page.setViewportSize({width: 390, height: 844});
        await page.evaluate(() => document.documentElement.setAttribute('data-bs-theme', 'dark'));
        const footer = await page.locator('#company-cancel-btn').boundingBox();
        assert.ok(footer.y >= 0 && footer.y + footer.height <= 844);
        await page.screenshot({path: path.join(artifacts, 'company-view-mobile.png')});
        await page.locator('#company-cancel-btn').click(); await modal.waitFor({state: 'hidden'});
        await page.setViewportSize({width: 1440, height: 1000});
        await page.locator('.edit-company').click(); await modal.waitFor({state: 'visible'});
        assert.equal(await page.locator('#company-name').evaluate(input => input.readOnly), false);
        assert.equal(await page.locator('#company-active').isEnabled(), true);
        assert.equal(await page.locator('#company-save-btn').isVisible(), true);
        await page.locator('#company-cancel-btn').click(); await modal.waitFor({state: 'hidden'});
        assert.equal(writes, 0);
        console.log('PASS: login home link removed; company eye modal is view-only and Edit works after closing it');

        const saved = await page.request.post(base + '/admin/consignments/save', {data: {rows: ['weight', 'volume'].map((mode, index) => ({
            consignment_number: prefix + '-' + index, company_id: company.id, measurement_mode: mode,
            weight: '10.123', volume: '9.876', pieces: 1, pickup_date: '2026-10-12',
            pickup_address: '429 Jagraj Gram, Delhi', drop_address: 'Delhi distribution centre', drop_pincode: '110037'}))}});
        assert.equal(saved.status(), 200);
        const rows = (await (await page.request.get(base + '/admin/consignments/list', {params: {search: prefix}})).json()).rows;
        ids = rows.map(row => row.id);
        await page.goto(base + '/admin/labels?' + new URLSearchParams({search: prefix}));
        for (const row of rows) {
            const text = await page.locator('tr').filter({has: page.locator('input[value="' + row.id + '"]')}).locator('td').last().textContent();
            assert.ok(text.includes(row.measurement_mode === 'volume' ? '9.876 ft³' : '10.123 kg'));
            assert.ok(!text.includes(row.measurement_mode === 'volume' ? 'kg' : 'ft³'));
        }
        await page.locator('#select-all-labels').check(); await page.locator('#review-labels-btn').click();
        await page.locator('#labelReviewModal').waitFor({state: 'visible'});
        for (const row of rows) {
            const card = page.locator('.label-review-card[data-id="' + row.id + '"]');
            const text = await card.locator('.label-snapshot').textContent();
            assert.ok(text.includes(row.measurement_mode === 'volume' ? '9.876 ft³' : '10.123 kg'));
            assert.ok(!text.includes(row.measurement_mode === 'volume' ? 'kg' : 'ft³'));
            await card.locator('[data-field=invoice_amount]').fill('6000.50');
        }
        await page.locator('#label-common-description').fill('Precision automotive spare parts, packed securely in reinforced cartons for business delivery.');
        await page.locator('#apply-label-common').click();
        const first = page.locator('.label-review-card').first();
        await first.locator('[data-field=eway_bill_exists]').check(); await first.locator('[data-field=eway_bill_number]').fill('123456789012');
        const download = page.waitForEvent('download'); await page.locator('#download-labels-btn').click();
        await (await download).saveAs(path.join(artifacts, 'weight-volume-labels.pdf'));
        console.log('PASS: label list/review show only the selected measure; real weight/volume PDF downloaded');

        const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==', 'base64');
        for (const kind of ['pod', 'invoice']) {
            const response = await page.request.post(base + '/admin/consignments/' + rows[0].id + '/' + kind, {multipart: {file: {name: rows[0].consignment_number + '_' + kind + '.png', mimeType: 'image/png', buffer: image}}});
            assert.equal(response.status(), 200); const reference = (await response.json())[kind === 'pod' ? 'pod_image' : 'invoice_file'];
            assert.equal(reference.split('/').pop(), rows[0].consignment_number + '_' + kind + '.png');
            const file = await page.request.get(base + '/admin/consignments/' + rows[0].id + '/' + kind);
            assert.ok(file.headers()['content-disposition'].includes(rows[0].consignment_number + '_' + kind + '.png'));
        }
        const backup = await page.request.get(base + '/admin/generate-backup'); assert.equal(backup.status(), 200);
        fs.writeFileSync(path.join(artifacts, 'complete-backup.zip'), await backup.body());
        assert.deepEqual(errors, []);
        console.log('PASS: named POD/invoice upload and individual download; complete backup downloaded');
        console.log('Artifacts: ' + artifacts);
    } finally {
        if (ids.length) await page.request.post(base + '/admin/consignments/save', {data: {rows: [], deleted_ids: ids}});
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
