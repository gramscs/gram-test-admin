// Run only against the disposable tests/browser/serve.py fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
    throw new Error('Set SYSTEM_TEST_URL to a disposable loopback app and SYSTEM_TEST_PASSWORD.');
}
(async () => {
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-followup-ui-'));
    fs.mkdirSync(artifacts, {recursive: true});
    const browser = await chromium.launch({headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {if (response.status() >= 500) errors.push(response.url());});
    const prefix = 'FOLLOWUP-' + Date.now();
    let ids = [], company;
    const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==', 'base64');
    async function mode(name, expected) {
        await page.locator(`[data-missing-filter="${name}"]`).click();
        assert.equal(await page.locator(`[data-missing-filter="${name}"]`).getAttribute('aria-pressed'), 'true');
        assert.equal(await page.locator('#missing-document-rows tr').count(), Math.min(expected, 25));
        assert.match(await page.locator('#missing-document-count').innerText(), new RegExp(`of ${expected} shipments$`));
    }
    try {
        await page.goto(base + '/admin/login');
        await page.locator('[name=email]').fill(process.env.SYSTEM_TEST_EMAIL || 'admin@example.test');
        await page.locator('[name=password]').fill(process.env.SYSTEM_TEST_PASSWORD);
        await page.locator('button[type=submit]').click();
        company = (await (await page.request.post(base + '/admin/companies', {data: {name: prefix + ' Client'}})).json()).company;
        const saved = await page.request.post(base + '/admin/consignments/save', {data: {rows: Array.from({length: 30}, (_, i) => ({consignment_number: prefix + '-' + String(i).padStart(2, '0'), identifier_type: 'AWB', company_id: company.id, status: i === 0 ? 'Delivered' : 'In Transit', pieces: 1, pickup_date: '2026-10-01'}))}});
        assert.equal(saved.status(), 200);
        const rows = (await (await page.request.get(base + '/admin/consignments/list', {params: {search: prefix, per_page: 100}})).json()).rows;
        ids = rows.map(row => row.id); assert.equal(ids.length, 30);
        const form = new FormData(); form.append('apply', 'true');
        for (const name of ['-00_pod', '-01_invoice', '-02_pod', '-02_invoice']) {
            form.append('files', new Blob([image], {type: 'image/png'}), prefix + name + '.png');
        }
        const upload = await page.request.post(base + '/admin/consignments/documents/bulk-upload', {multipart: form});
        assert.equal(upload.status(), 200, await upload.text());
        const dashboard = new URL('/admin/dashboard', base);
        dashboard.search = new URLSearchParams({search: prefix, company: company.id, identifier_type: 'AWB'}).toString();
        await page.goto(dashboard.toString());
        assert.equal(await page.locator('#document-followup').getAttribute('open'), null);
        await page.locator('#document-followup > summary').focus(); await page.keyboard.press('Enter');
        assert.notEqual(await page.locator('#document-followup').getAttribute('open'), null);
        await mode('any', 29); await mode('pod', 28); await mode('invoice', 28); await mode('both', 27);
        assert.equal(await page.locator('#missing-document-rows td:nth-child(2)').first().innerText(), 'AWB');
        assert.equal(await page.locator('#missing-document-rows td:nth-child(3)').first().innerText(), company.name);
        await page.locator('#missing-document-next').click();
        assert.equal(await page.locator('#missing-document-rows tr').count(), 2);
        assert.match(await page.locator('#missing-document-page').innerText(), /Page 2 of 2/);
        await mode('invoice', 28);
        assert.match(await page.locator('#missing-document-page').innerText(), /Page 1 of 2/);
        console.log('PASS: missing-POD/invoice/either/both lists, identifier type, pagination and keyboard expansion');
        const link = page.locator('#missing-document-rows a').first();
        const href = new URL(await link.getAttribute('href'));
        assert.equal(href.searchParams.get('identifier_type'), 'AWB');
        assert.equal(href.searchParams.get('company'), String(company.id));
        await link.click(); await page.waitForURL('**/admin/documents?**');
        await page.waitForFunction(() => document.querySelectorAll('#sheet-body tr[data-id]').length === 1);
        console.log('PASS: Open Documents targets the original shipment and its company/type');
        await page.goto(dashboard.toString() + '&status=Delivered&period=custom&start=2026-10-01&end=2026-10-02');
        await page.locator('#document-followup > summary').click();
        assert.equal(await page.locator('#missing-document-rows tr').count(), 1);
        assert.match(await page.locator('#missing-document-rows').innerText(), new RegExp(prefix + '-00'));
        await page.locator('[data-missing-filter="pod"]').click();
        assert.equal(await page.locator('#missing-document-count').innerText(), '0 shipments');
        assert.match(await page.locator('#missing-document-rows').innerText(), /No shipments match/);
        console.log('PASS: dashboard date/status scope and empty missing-document category');
        await page.goto(dashboard.toString()); await page.locator('#document-followup > summary').click();
        await page.locator('#document-followup').screenshot({path: path.join(artifacts, 'followup-light.png')});
        await page.locator('[data-theme-toggle]').click(); await page.waitForTimeout(300);
        await page.locator('#document-followup').screenshot({path: path.join(artifacts, 'followup-dark.png')});
        for (const width of [390, 768, 1440]) {
            await page.setViewportSize({width, height: 1000});
            await page.locator('[data-missing-filter="both"]').click();
            const viewport = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
            assert.ok(viewport.scroll <= viewport.width + 1, JSON.stringify(viewport));
            await page.locator('#missing-document-next').click();
            assert.match(await page.locator('#missing-document-page').innerText(), /Page 2 of 2/);
        }
        await page.setViewportSize({width: 390, height: 1000});
        await page.locator('#document-followup').screenshot({path: path.join(artifacts, 'followup-mobile.png')});
        console.log('PASS: light/dark theme and mobile/tablet/desktop controls without page overflow');
        assert.deepEqual(errors, []);
    } finally {
        if (ids.length) await page.request.post(base + '/admin/consignments/save', {data: {rows: [], deleted_ids: ids}});
        if (company) await page.request.post(base + '/admin/companies/' + company.id + '/archive', {data: {}});
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
