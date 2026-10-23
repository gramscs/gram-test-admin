// Run only against a disposable local app. See docs/system-testing.md.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
const password = process.env.SYSTEM_TEST_PASSWORD;
if (!base || !password || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
    throw new Error('Set SYSTEM_TEST_URL to a disposable loopback app and SYSTEM_TEST_PASSWORD to its test password.');
}

(async () => {
    const browser = await chromium.launch({
        headless: true,
        ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
        args: ['--no-sandbox'],
    });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let shipment;
    try {
        await page.goto(base + '/admin/login');
        await page.locator('[name=email]').fill(process.env.SYSTEM_TEST_EMAIL || 'admin@example.test');
        await page.locator('[name=password]').fill(password);
        await page.locator('button[type=submit]').click();
        await page.waitForURL('**/admin/dashboard');
        const identifier = 'STAGING-' + Date.now();
        const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==', 'base64');
        const created = await page.request.post(base + '/admin/consignments/save', {
            data: { rows: [{ consignment_number: identifier, status: 'In Transit', pieces: 1 }] },
        });
        assert.equal(created.status(), 200);
        const list = async () => (await (await page.request.get(base + '/admin/consignments/list?search=' + identifier)).json()).rows[0];
        shipment = await list();
        await page.goto(base + '/admin/consignments');
        await page.locator('#search-input').fill(identifier);
        await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');

        async function openModalDuringRefresh() {
            const original = await page.locator('#sheet-body tr').elementHandle();
            await page.locator('.edit-row').click();
            await page.locator('#editConsignmentModal.show').waitFor();
            // Reproduce a delayed search/post-save refresh replacing the row
            // that the open modal still points to. Do not rely on timer races.
            const fetched = page.waitForResponse(response => response.url().includes('/consignments/list?'));
            await page.locator('#search-input').dispatchEvent('input');
            assert.equal((await fetched).status(), 200);
            await page.waitForFunction(element => !element.isConnected, original);
        }
        async function save() {
            await page.locator('#modal-save-btn').click();
            await page.locator('#editConsignmentModal').waitFor({ state: 'hidden' });
            const saved = page.waitForResponse(response => response.url().endsWith('/consignments/save') && response.request().method() === 'POST');
            await page.locator('#save-btn').click();
            const response = await saved;
            assert.equal(response.status(), 200);
            assert.equal((await response.json()).success, true);
            return response.request().postDataJSON().rows.find(row => String(row.id) === String(shipment.id));
        }
        await openModalDuringRefresh();
        await page.locator('#modal-pieces').fill('7');
        await page.locator('#modal-pickup-address').fill('Address edited during refresh');
        for (const kind of ['pod', 'invoice']) {
            await page.locator('#modal-' + kind + '-file').setInputFiles({ name: kind + '.png', mimeType: 'image/png', buffer: image });
            await page.waitForFunction(k => document.querySelector('#modal-' + k + '-status').textContent.includes('awaiting Save All'), kind);
        }
        const uploaded = await save();
        assert(uploaded.pod_file_data && uploaded.invoice_file_data);
        const persisted = await list();
        assert.equal(persisted.pieces, 7);
        assert.equal(persisted.pickup_address, 'Address edited during refresh');
        assert(persisted.pod_image && persisted.invoice_file);
        for (const kind of ['pod', 'invoice']) {
            const response = await page.request.get(base + '/admin/consignments/' + shipment.id + '/' + kind);
            assert.equal(response.status(), 200);
            assert.equal(response.headers()['content-type'], 'image/png');
        }
        console.log('PASS: refreshed table preserves modal fields and both document uploads.');

        await page.reload();
        await page.locator('#search-input').fill(identifier);
        await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
        await openModalDuringRefresh();
        for (const kind of ['pod', 'invoice']) await page.locator('#modal-' + kind + '-remove').click();
        const removed = await save();
        assert.equal(removed.pod_remove, true);
        assert.equal(removed.invoice_remove, true);
        const final = await list();
        assert.equal(final.pod_image, null);
        assert.equal(final.invoice_file, null);
        assert.equal(final.pieces, 7);
        assert.deepEqual(errors, []);
        console.log('PASS: refreshed table preserves staged document removals.');
    } finally {
        if (shipment) {
            const cleanup = await page.request.post(base + '/admin/consignments/save', { data: { rows: [], deleted_ids: [shipment.id] } });
            assert.equal(cleanup.status(), 200);
        }
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
