// Functional UI coverage; run against tests/browser/serve.py, not operational data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
const password = process.env.SYSTEM_TEST_PASSWORD;
if (!base || !password || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
    throw new Error('Use a disposable loopback app and set SYSTEM_TEST_URL and SYSTEM_TEST_PASSWORD.');
}
function proofPDF() {
    const stream = 'BT /F1 14 Tf 72 700 Td (Browser test POD) Tj ET';
    const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        '<< /Length ' + stream.length + ' >>\nstream\n' + stream + '\nendstream', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
    let data = '%PDF-1.4\n'; const offsets = [];
    objects.forEach((object, index) => { offsets.push(data.length); data += `${index + 1} 0 obj\n${object}\nendobj\n`; });
    const xref = data.length;
    data += 'xref\n0 6\n0000000000 65535 f \n' + offsets.map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('');
    return Buffer.from(data + `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}
(async () => {
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-document-browser-'));
    fs.mkdirSync(artifacts, {recursive: true});
    const browser = await chromium.launch({headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, acceptDownloads: true});
    const errors = [], failures = [], results = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500) failures.push(response.url()); });
    let ids = [], companies = [], first;
    const prefix = 'UI-DOC-' + Date.now();
    const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==', 'base64');
    async function check(name, work) { await work(); results.push(name); console.log('PASS: ' + name); }
    async function download(locator, name) {
        const event = page.waitForEvent('download'); await locator.click(); const file = await event;
        assert.equal(await file.failure(), null); await file.saveAs(path.join(artifacts, name));
    }
    async function list(search) { return (await (await page.request.get(base + '/admin/consignments/list', {params: {search, per_page: 100}})).json()).rows; }
    async function saveClick() {
        const event = page.waitForResponse(response => response.url().endsWith('/consignments/save') && response.request().method() === 'POST');
        await page.locator('#save-btn').click(); const response = await event; assert.equal(response.status(), 200); assert.equal((await response.json()).success, true);
    }
    try {
        await page.goto(base + '/admin/login'); await page.locator('[name=email]').fill(process.env.SYSTEM_TEST_EMAIL || 'admin@example.test');
        await page.locator('[name=password]').fill(password); await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        for (const letter of ['A', 'B']) {
            const result = await page.request.post(base + '/admin/companies', {data: {name: prefix + '-Company-' + letter}});
            assert.equal(result.status(), 200); companies.push((await result.json()).company);
        }
        const rows = Array.from({length: 12}, (_, index) => ({consignment_number: prefix + (index ? '-AWB-' + index : '-FIRST'), identifier_type: 'AWB', company_id: companies[0].id, status: 'In Transit', pieces: 1}));
        rows.push({consignment_number: prefix + '-LRN', identifier_type: 'LRN', company_id: companies[0].id, status: 'In Transit'},
            {consignment_number: prefix + '-OTHER', identifier_type: 'AWB', company_id: companies[1].id, status: 'In Transit'},
            {consignment_number: prefix + '-ORDER', identifier_type: 'Order ID', status: 'In Transit'});
        const response = await page.request.post(base + '/admin/consignments/save', {data: {rows}}); assert.equal(response.status(), 200);
        const created = await list(prefix); ids = created.map(row => row.id); first = created.find(row => row.consignment_number.endsWith('-FIRST'));
        assert.equal(created.length, 15); assert(first);

        await check('Shipments navigation, combined filters, pagination and persistence', async () => {
            await page.getByRole('link', {name: 'Shipments', exact: true}).click(); await page.waitForURL('**/admin/shipments');
            await page.locator('#search-input').fill(prefix); await page.locator('#company-filter').selectOption(String(companies[0].id)); await page.locator('#identifier-type-filter').selectOption('AWB');
            await page.waitForFunction(() => document.querySelector('#total-count').textContent === '12');
            await page.locator('#next-page-btn').click(); await page.waitForFunction(() => document.querySelector('#showing-start').textContent === '11');
            await page.locator('#identifier-type-filter').selectOption('LRN'); await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
            assert.equal(await page.locator('#showing-start').innerText(), '1'); await page.reload();
            assert.equal(await page.locator('#company-filter').inputValue(), String(companies[0].id)); assert.equal(await page.locator('#identifier-type-filter').inputValue(), 'LRN');
            await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
        });
        await check('Hidden staged edits survive filter changes and Save All keeps the filtered page', async () => {
            await page.locator('#identifier-type-filter').selectOption('AWB'); await page.waitForFunction(() => document.querySelector('#total-count').textContent === '12');
            const id = await page.locator('#sheet-body tr').first().getAttribute('data-id'); await page.locator('#sheet-body .pieces').first().fill('7');
            await page.locator('#identifier-type-filter').selectOption('LRN'); await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1'); await saveClick();
            await page.waitForFunction(() => document.querySelector('#showing-start').textContent === '1'); assert.equal((await list(prefix)).find(row => String(row.id) === id).pieces, 7);
            await page.locator('#data-tools-btn').click(); await download(page.locator('#shipment-excel-export'), 'filtered-shipments.xlsx');
            await page.locator('#data-tools-btn').click(); await download(page.locator('#shipment-pdf-export'), 'filtered-shipments.pdf');
            const target = new URL(await page.locator('#shipment-pdf-export').getAttribute('href')); assert.equal(target.searchParams.get('identifier_type'), 'LRN'); assert.equal(target.searchParams.get('company'), String(companies[0].id));
        });
        await check('Documents show linked identifier type, missing flags and the shared bulk upload', async () => {
            await page.getByRole('link', {name: 'Documents', exact: true}).click(); await page.locator('#search-input').fill(first.consignment_number); await page.locator('#company-filter').selectOption(String(companies[0].id)); await page.locator('#identifier-type-filter').selectOption('AWB');
            await page.waitForFunction(() => document.querySelector('#document-count').textContent.endsWith('of 1 shipments'));
            assert.equal(await page.getByText('Not uploaded', {exact: true}).count(), 2); assert.equal((await page.locator('#sheet-body td').nth(2).innerText()).trim(), 'AWB');
            await page.getByRole('button', {name: /Bulk documents/}).click();
            await page.locator('#bulk-document-files').setInputFiles([
                {name: first.consignment_number + '_pod.pdf', mimeType: 'application/pdf', buffer: proofPDF()},
                {name: first.consignment_number + '_invoice.png', mimeType: 'image/png', buffer: image},
            ]);
            await page.locator('#bulk-review-btn').click(); await page.waitForFunction(() => !document.querySelector('#bulk-upload-btn').disabled);
            assert.equal(await page.locator('#bulk-document-review li').count(), 2); await page.locator('#bulk-upload-btn').click();
            await page.waitForFunction(() => document.querySelector('#bulk-documents-feedback').textContent.includes('uploaded and saved'));
            await page.waitForFunction(() => document.querySelectorAll('.document-name').length === 2);
            await page.locator('#bulkDocumentsModal .modal-footer button').click();
            assert.equal(await page.getByText('Not uploaded', {exact: true}).count(), 0); await download(page.getByRole('link', {name: 'Download POD for ' + first.consignment_number}), 'pod.pdf');
            assert.deepEqual(await page.locator('.document-storage').allTextContents(), ['Saved locally', 'Saved locally']);
            await download(page.getByRole('link', {name: 'Download INVOICE for ' + first.consignment_number}), 'invoice.png');
            await page.locator('[data-theme-toggle]').click();
            await page.locator('html[data-bs-theme="dark"]').waitFor();
            // Let the button color transition finish before capturing the theme.
            await page.waitForTimeout(250);
            await page.screenshot({path: path.join(artifacts, 'documents-dark.png'), fullPage: true});
        });
        await check('Documents bulk download follows company/type/search filters', async () => {
            await page.getByRole('button', {name: /Bulk documents/}).click(); await page.locator('#bulk-download-scope').selectOption('filtered');
            const request = page.waitForRequest(request => request.url().endsWith('/documents/bulk-download')); await download(page.locator('#bulk-download-btn'), 'filtered-documents.zip');
            const filters = (await request).postDataJSON(); assert.equal(filters.company, String(companies[0].id)); assert.equal(filters.identifier_type, 'AWB'); assert.equal(filters.search, first.consignment_number);
            await page.locator('#bulkDocumentsModal .modal-footer button').click();
        });
        await check('Document selections survive pagination', async () => {
            await page.locator('#search-input').fill(prefix); await page.locator('#per-page-select').selectOption('10');
            await page.waitForFunction(() => document.querySelector('#document-count').textContent.endsWith('of 12 shipments'));
            await page.locator('#select-visible-shipments').check(); await page.locator('#next-page-btn').click(); await page.waitForFunction(() => document.querySelector('#document-page').textContent === 'Page 2 of 2');
            await page.locator('#select-visible-shipments').check(); assert.equal(await page.locator('#bulk-selected-count').innerText(), '12');
            await page.locator('#prev-page-btn').click(); await page.waitForFunction(() => document.querySelector('#document-page').textContent === 'Page 1 of 2'); assert.equal(await page.locator('.select-shipment:checked').count(), 10);
        });
        await check('Editing identifier in Shipments updates Documents without a separate copy', async () => {
            await page.locator('#search-input').fill(first.consignment_number); await page.waitForFunction(() => document.querySelector('#document-count').textContent.endsWith('of 1 shipments'));
            await page.getByRole('link', {name: 'View shipment', exact: true}).click(); await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
            await page.locator('#sheet-body .identifier_type').selectOption('Order ID'); await saveClick();
            await page.goto(base + '/admin/documents?' + new URLSearchParams({company: String(companies[0].id), identifier_type: 'AWB', search: first.consignment_number}));
            await page.waitForFunction(() => document.querySelector('#document-count').textContent.endsWith('of 0 shipments')); await page.locator('#identifier-type-filter').selectOption('Order ID');
            await page.waitForFunction(() => document.querySelector('#document-count').textContent.endsWith('of 1 shipments'));
            assert.equal((await page.locator('#sheet-body td').nth(2).innerText()).trim(), 'Order ID'); assert.equal(await page.locator('.document-name').count(), 2);
        });
        await check('Documents responsive layout and filter reset', async () => {
            for (const width of [390, 768, 1440]) {
                await page.setViewportSize({width, height: 844}); const sizes = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth})); assert(sizes.scroll <= sizes.width + 1);
                await page.getByRole('button', {name: /Bulk documents/}).click(); await page.locator('#bulk-review-btn').click({trial: true}); await page.locator('#bulkDocumentsModal .modal-footer button').click();
            }
            await page.locator('#clear-filters-btn').click(); await page.waitForFunction(() => document.querySelector('#company-filter').value === '' && document.querySelector('#identifier-type-filter').value === '');
            assert.equal(await page.locator('#search-input').inputValue(), ''); assert(!new URL(page.url()).searchParams.has('company'));
        });
        assert.deepEqual(errors, []); assert.deepEqual(failures, []);
        fs.writeFileSync(path.join(artifacts, 'results.json'), JSON.stringify({passed: results, errors, failures, shipment: first.consignment_number}, null, 2));
        console.log('Artifacts: ' + artifacts);
    } finally {
        if (ids.length) await page.request.post(base + '/admin/consignments/save', {data: {rows: [], deleted_ids: ids}});
        for (const company of companies) await page.request.post(base + '/admin/companies/' + company.id + '/archive', {data: {}});
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
