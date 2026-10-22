// Run against the disposable tests/browser/serve.py fixture, never operational data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
    throw new Error('Set SYSTEM_TEST_URL to a disposable loopback app and SYSTEM_TEST_PASSWORD.');
}
function pdf() {
    const stream = 'BT /F1 14 Tf 72 700 Td (Document editor test) Tj ET';
    const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        '<< /Length ' + stream.length + ' >>\nstream\n' + stream + '\nendstream', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
    let data = '%PDF-1.4\n'; const offsets = [];
    objects.forEach((object, i) => {offsets.push(data.length); data += `${i + 1} 0 obj\n${object}\nendobj\n`;});
    const xref = data.length;
    return Buffer.from(data + 'xref\n0 6\n0000000000 65535 f \n' + offsets.map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('') + `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}
(async () => {
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'gram-editor-ui-'));
    fs.mkdirSync(artifacts, {recursive: true});
    const browser = await chromium.launch({headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? {executablePath: process.env.CHROMIUM_PATH} : {})});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}, acceptDownloads: true});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {if (response.status() >= 500) errors.push(response.url());});
    const prefix = 'EDIT-DOC-' + Date.now();
    let ids = [], shipment;
    const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZQAAAABJRU5ErkJggg==', 'base64');
    const modal = page.locator('#documentEditorModal');
    async function current() {return (await (await page.request.get(base + '/admin/consignments/list', {params: {shipment_id: shipment.id}})).json()).rows[0];}
    async function open(kind, attached = true) {
        await page.getByRole('button', {name: (attached ? 'Manage ' : 'Upload ') + kind.toUpperCase() + ' for ' + prefix, exact: true}).click();
        await modal.waitFor({state: 'visible'});
    }
    async function choose(name, buffer, mimeType) {
        await page.locator('#document-editor-file').setInputFiles({name, mimeType, buffer});
        await page.waitForFunction(() => !document.querySelector('#document-editor-save').disabled);
    }
    async function save(kind, method = 'POST', status = 200) {
        const response = page.waitForResponse(response => new URL(response.url()).pathname === '/admin/consignments/' + shipment.id + '/' + kind && response.request().method() === method);
        await page.locator('#document-editor-save').click(); assert.equal((await response).status(), status);
        if (status === 200) {await modal.waitFor({state: 'hidden'}); await page.waitForFunction(() => document.querySelector('#document-count').textContent.startsWith('Showing'));}
    }
    async function cancel() {await modal.getByRole('button', {name: 'Cancel', exact: true}).click(); await modal.waitFor({state: 'hidden'});}
    try {
        await page.goto(base + '/admin/login'); await page.locator('[name=email]').fill(process.env.SYSTEM_TEST_EMAIL || 'admin@example.test');
        await page.locator('[name=password]').fill(process.env.SYSTEM_TEST_PASSWORD); await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        const saved = await page.request.post(base + '/admin/consignments/save', {data: {rows: [prefix, prefix + '-EXTRA'].map(consignment_number => ({consignment_number, identifier_type: 'AWB', pieces: 4, pickup_address: 'Keep this pickup', status: 'In Transit'}))}});
        assert.equal(saved.status(), 200);
        const rows = (await (await page.request.get(base + '/admin/consignments/list', {params: {search: prefix, per_page: 100}})).json()).rows;
        ids = rows.map(row => row.id); shipment = rows.find(row => row.consignment_number === prefix);
        const url = base + '/admin/documents?' + new URLSearchParams({search: prefix, identifier_type: 'AWB'});
        await page.goto(url); await page.waitForFunction(() => document.querySelector('#document-count').textContent.endsWith('of 2 shipments'));
        await open('pod', false); await choose('proof.pdf', pdf(), 'application/pdf'); await save('pod');
        await open('invoice', false); await choose('invoice.png', image, 'image/png'); await save('invoice');
        assert.equal((await current()).pieces, 4); assert.equal((await current()).pickup_address, 'Keep this pickup');
        console.log('PASS: upload PDF POD and image invoice from Documents without changing shipment data');
        const before = await current();
        await open('pod'); await page.locator('#document-editor-file').setInputFiles({name: 'unsafe.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg/>')});
        await page.locator('#document-editor-error:not(.d-none)').waitFor(); assert.equal(await page.locator('#document-editor-save').isDisabled(), true);
        await choose('replacement.png', image, 'image/png'); await cancel(); assert.equal((await current()).pod_image, before.pod_image);
        await open('pod'); await choose('replacement.png', image, 'image/png'); await save('pod');
        assert.notEqual((await current()).pod_image, before.pod_image); assert.equal((await current()).invoice_file, before.invoice_file);
        assert.equal(await page.getByText('replacement.png', {exact: true}).count(), 1);
        console.log('PASS: invalid type rejected, Cancel preserves documents, replacement affects only POD');
        await open('invoice'); await page.locator('#document-editor-remove').click(); await page.locator('#document-editor-undo').click();
        assert.equal(await page.locator('#document-editor-save').isDisabled(), true); assert.equal((await current()).invoice_file, before.invoice_file);
        await page.locator('#document-editor-remove').click(); await save('invoice', 'DELETE'); assert.equal((await current()).invoice_file, null);
        await open('invoice', false); await choose('new-invoice.pdf', pdf(), 'application/pdf'); await save('invoice');
        console.log('PASS: staged removal, Undo, saved removal and re-upload');
        await open('pod');
        const newer = await page.request.post(base + '/admin/consignments/' + shipment.id + '/pod', {multipart: {file: {name: 'newer.pdf', mimeType: 'application/pdf', buffer: pdf()}}});
        assert.equal(newer.status(), 200); const reference = (await newer.json()).pod_image;
        await page.locator('#document-editor-remove').click(); await save('pod', 'DELETE', 409);
        await page.locator('#document-editor-error:not(.d-none)').waitFor({state: 'visible'});
        assert.match(await page.locator('#document-editor-error').innerText(), /changed since you opened/); assert.equal((await current()).pod_image, reference);
        await cancel(); await page.reload(); await page.waitForFunction(() => document.querySelector('#document-count').textContent.startsWith('Showing'));
        console.log('PASS: stale editor cannot remove a newer document');
        await open('pod');
        const downloadEvent = page.waitForEvent('download'); await page.locator('#document-editor-download').click(); const download = await downloadEvent;
        assert.equal(await download.failure(), null); await download.saveAs(path.join(artifacts, 'managed-pod.pdf'));
        await modal.screenshot({path: path.join(artifacts, 'editor-light.png')}); await cancel();
        await page.locator('[data-theme-toggle]').click(); await page.waitForTimeout(300);
        for (const width of [390, 768, 1440]) {
            await page.setViewportSize({width, height: 844}); await open('pod'); await choose('unsaved.pdf', pdf(), 'application/pdf');
            await page.locator('#document-editor-save').click({trial: true});
            assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
            if (width === 390) await modal.screenshot({path: path.join(artifacts, 'editor-mobile-dark.png')});
            await cancel();
        }
        console.log('PASS: current download, light/dark modal and reachable mobile/tablet/desktop save buttons');
        const eye = page.locator('tr[data-id="' + shipment.id + '"]').getByRole('link', {name: 'View shipment', exact: true});
        assert.equal(await eye.innerText(), ''); assert.equal(await eye.locator('.fa-eye').count(), 1);
        await eye.click(); await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
        assert.equal(new URL(page.url()).searchParams.get('shipment_id'), String(shipment.id));
        assert.equal(await page.locator('#sheet-body tr[data-id]').getAttribute('data-id'), String(shipment.id));
        await page.reload(); await page.waitForFunction(() => document.querySelector('#total-count').textContent === '1');
        await page.locator('#search-input').fill(prefix); await page.locator('#identifier-type-filter').selectOption('LRN');
        await page.waitForFunction(() => !new URL(location.href).searchParams.has('shipment_id'));
        await page.locator('#clear-filters-btn').click(); await page.waitForFunction(() => Number(document.querySelector('#total-count').textContent) > 1);
        console.log('PASS: eye opens exactly one shipment despite overlapping identifiers; refresh and clearing scope work');
        assert.deepEqual(errors, []);
    } finally {
        if (ids.length) await page.request.post(base + '/admin/consignments/save', {data: {rows: [], deleted_ids: ids}});
        await browser.close();
    }
})().catch(error => {console.error(error); process.exitCode = 1;});
