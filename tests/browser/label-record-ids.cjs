// ID recovery regression against the disposable tests/browser/serve.py app.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use the disposable loopback app.');

(async () => {
    const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox']});
    const page = await browser.newPage(); const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-label-ids-'));
    fs.mkdirSync(artifacts, {recursive: true});
    try {
        await page.goto(base + '/admin/login'); await page.locator('#email').fill('admin@example.test');
        await page.locator('#password').fill(process.env.SYSTEM_TEST_PASSWORD);
        await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        await page.goto(base + '/admin/labels');
        const valid = await page.evaluate(() => JSON.parse(document.querySelector('#label-page-data').textContent)[0]);
        assert.equal(await page.locator('#labels-form').getAttribute('data-id-format'), 'uuid');
        await page.evaluate(row => sessionStorage.setItem('gram-label-selection:admin', JSON.stringify({
            selected: [{id: '17', pieces: 1, consignment_number: 'OLD-SQLITE'}, {id: 'None', pieces: 1}, row],
            drafts: {[row.id]: {invoice_amount: '6000.50', payment_mode: 'pre-paid', goods_description: 'Saved goods description', eway_bill_exists: false, eway_bill_number: ''}},
            review: [{id: '17', pieces: 1}], job: '00000000-0000-4000-8000-000000000017'
        })), valid);
        await page.reload();
        assert.equal(await page.locator('#label-selected-count').textContent(), '1');
        assert.ok((await page.locator('#label-selection-notice').textContent()).includes('outdated'));
        await page.locator('#review-labels-btn').click(); await page.locator('#labelReviewModal').waitFor({state: 'visible'});
        assert.equal(await page.locator('[data-field=invoice_amount]').inputValue(), '6000.50');
        assert.equal(await page.locator('[data-field=goods_description]').inputValue(), 'Saved goods description');
        console.log('PASS: incompatible cached IDs discarded; current selection and manual details preserved');

        // A UI/widget identifier must never replace the server's shipment UUID.
        await page.evaluate(() => {
            const card = document.querySelector('.label-review-card'); card.dataset.id = 'label-card-0';
            const unrelated = document.createElement('input'); unrelated.type = 'hidden';
            unrelated.dataset.field = 'id'; unrelated.value = 'label-amount-0'; card.append(unrelated);
        });
        await page.locator('[data-field=invoice_amount]').fill('6500.25');
        const request = page.waitForRequest(request => request.url().endsWith('/labels/generate'));
        const download = page.waitForEvent('download'); await page.locator('#download-labels-btn').click();
        const payload = (await request).postDataJSON();
        assert.deepEqual(payload.consignment_ids, [valid.id]);
        assert.equal(payload.label_details[0].id, valid.id);
        assert.equal(payload.label_details[0].invoice_amount, '6500.25');
        const file = await download; assert.equal(await file.failure(), null);
        await file.saveAs(path.join(artifacts, 'recovered-labels.pdf'));
        await page.waitForFunction(() => !document.querySelector('#download-labels-btn').disabled);
        const state = await page.evaluate(() => JSON.parse(sessionStorage.getItem('gram-label-selection:admin')));
        assert.equal(state.drafts[valid.id].invoice_amount, '6500.25');
        assert.equal(state.drafts['label-card-0'], undefined);
        console.log('PASS: PDF generation uses the actual shipment ID despite altered form IDs or unrelated fields');
        const job = state.job;
        const popup = page.waitForEvent('popup'); await page.locator('#preview-labels-btn').click();
        const preview = await popup; await page.waitForFunction(() => !document.querySelector('#download-labels-btn').disabled);
        await preview.close();
        assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('gram-label-selection:admin')).job), job);
        await page.getByRole('button', {name: 'Back', exact: true}).click(); await page.reload();
        await page.locator('#review-labels-btn').click(); await page.locator('#labelReviewModal').waitFor({state: 'visible'});
        assert.equal(await page.locator('[data-field=invoice_amount]').inputValue(), '6500.25');
        assert.deepEqual(errors, []);
        console.log('PASS: preview/reload retain invoice details and numbering, with no JavaScript errors');
    } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
