// Validation must remain visible in the active dialog. Use disposable serve.py.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.SYSTEM_TEST_URL;
if (!base || !process.env.SYSTEM_TEST_PASSWORD || !['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw new Error('Use the disposable loopback test app.');
(async () => {
    const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium', args:['--no-sandbox']});
    const page = await browser.newPage({viewport:{width:1440,height:1000}}), errors=[];
    page.on('pageerror', error => errors.push(error.message));
    const artifacts = process.env.SYSTEM_TEST_ARTIFACTS_DIR || '/tmp/gram-modal-validation'; fs.mkdirSync(artifacts,{recursive:true});
    const modal = page.locator('#editConsignmentModal'), alert = page.locator('#shipment-form-error');
    async function visibleError(text, field) {
        await alert.waitFor({state:'visible'});
        await page.waitForFunction(()=>getComputedStyle(document.getElementById('editConsignmentModal')).opacity==='1');
        assert.ok((await alert.textContent()).includes(text));
        assert.equal(await modal.isVisible(),true);
        assert.equal(await alert.evaluate(element => {const r=element.getBoundingClientRect(); return r.top>=0 && r.bottom<=innerHeight && r.right<=innerWidth;}),true);
        if (field) {
            assert.equal(await page.locator('#'+field).getAttribute('aria-invalid'),'true');
            assert.ok((await page.locator('#'+field).getAttribute('aria-describedby')).includes('shipment-form-error'));
            assert.equal(await page.locator('#'+field).evaluate(input=>input===document.activeElement),true);
        }
    }
    try {
        await page.goto(base+'/admin/login'); await page.locator('#email').fill('admin@example.test'); await page.locator('#password').fill(process.env.SYSTEM_TEST_PASSWORD);
        await page.locator('button[type=submit]').click(); await page.waitForURL('**/admin/dashboard');
        await page.goto(base+'/admin/consignments');
        await page.locator('#sheet-body .edit-row').first().waitFor();
        await page.locator('#add-row-btn').click(); await modal.waitFor({state:'visible'});
        await page.locator('#modal-save-btn').click(); await visibleError('cannot be empty','modal-consignment-number');
        assert.equal(await page.evaluate(()=>adminState.locallyAddedRows.length),0);
        await page.screenshot({path:path.join(artifacts,'shipment-required-desktop.png')});
        await page.locator('#modal-consignment-number').fill('MODAL-'+Date.now());
        await page.locator('#modal-pickup-address').fill('Keep this address while correcting errors');
        for (const [id,value,message] of [
            ['modal-pickup-pincode','012345','Pickup Pincode'], ['modal-drop-pincode','123','Drop Pincode'],
            ['modal-pieces','0','Boxes/pallets must'], ['modal-pieces','1.5','Boxes/pallets must'],
            ['modal-chargeable-weight','-1','Weight'], ['modal-chargeable-weight','0.0001','Weight'],
            ['modal-chargeable-weight','1000000000','Weight']]) {
            await page.locator('#modal-pickup-pincode').fill(''); await page.locator('#modal-drop-pincode').fill('');
            await page.locator('#modal-pieces').fill('1'); await page.locator('#modal-chargeable-weight').fill('10.123');
            await page.locator('#'+id).fill(value); await page.locator('#modal-save-btn').click();
            await visibleError(message,id);
            assert.equal(await page.locator('#modal-pickup-address').inputValue(),'Keep this address while correcting errors');
            assert.equal(await page.evaluate(()=>adminState.locallyAddedRows.length),0);
        }
        await page.setViewportSize({width:390,height:844}); await page.evaluate(()=>document.documentElement.setAttribute('data-bs-theme','dark'));
        await page.locator('#modal-chargeable-weight').fill('10.123'); await page.locator('#modal-measurement-mode').selectOption('volume');
        await page.locator('#modal-chargeable-volume').fill('-1'); await page.locator('#modal-save-btn').click(); await visibleError('Volume','modal-chargeable-volume');
        await page.screenshot({path:path.join(artifacts,'shipment-number-mobile-dark.png')});
        const footer=await page.locator('#modal-save-btn').boundingBox(); assert.ok(footer.y+footer.height<=844);
        await page.locator('#modal-chargeable-volume').fill('1.234');
        await page.locator('#modal-measurement-mode').selectOption('weight');
        await page.locator('#modal-chargeable-weight').fill('-1');
        await page.locator('#modal-measurement-mode').selectOption('volume');
        await page.locator('#modal-save-btn').click(); await visibleError('Switch the measurement selector to Weight','modal-measurement-mode');
        assert.equal(await page.locator('#modal-measurement-mode').inputValue(),'volume');
        assert.equal(await page.locator('#modal-chargeable-weight').inputValue(),'-1');
        await page.locator('#modal-measurement-mode').selectOption('weight');
        await page.locator('#modal-chargeable-weight').fill('10.123');
        await page.locator('#modal-measurement-mode').selectOption('volume');
        await page.locator('#modal-pod-file').setInputFiles({name:'bad.txt',mimeType:'text/plain',buffer:Buffer.from('invalid document')});
        await page.locator('#modal-save-btn').click(); await visibleError('Check the selected documents');
        await page.waitForTimeout(10500); await visibleError('Check the selected documents');
        assert.equal(await page.locator('#modal-pickup-address').inputValue(),'Keep this address while correcting errors');
        await page.screenshot({path:path.join(artifacts,'shipment-document-error-mobile.png')});
        await modal.getByRole('button',{name:'Cancel',exact:true}).click(); await modal.waitFor({state:'hidden'});
        await page.locator('#add-row-btn').click(); await modal.waitFor({state:'visible'});
        assert.equal(await alert.isVisible(),false); assert.equal(await modal.locator('[aria-invalid=true]').count(),0);
        await modal.getByRole('button',{name:'Cancel',exact:true}).click(); await modal.waitFor({state:'hidden'});
        console.log('PASS: required identifier, PINs, pieces, weight/volume and document errors remain in the modal; drafts preserved, no invalid row staged, mobile controls reachable and errors persist past auto-dismiss.');

        await page.setViewportSize({width:1440,height:1000});
        await page.locator('#sheet-body .edit-row').first().click(); await modal.waitFor({state:'visible'});
        await page.locator('#modal-pieces').fill('4'); await page.locator('#modal-save-btn').click(); await modal.waitFor({state:'hidden'});
        assert.equal(await alert.isVisible(),false);
        let release; const held = new Promise(resolve=>{release=resolve;});
        await page.route('**/admin/consignments/save',async route=>{await held; await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({success:false,message:'Validation errors. Please fix highlighted rows.',errors:[{index:0,field:'shipment',message:'Weight must be non-negative with up to 3 decimal places.'}]})});});
        const request=page.waitForRequest(request=>request.url().endsWith('/consignments/save'));
        await page.locator('#save-btn').click(); await request;
        await page.locator('#sheet-body .edit-row').first().click(); await modal.waitFor({state:'visible'});
        await page.locator('#modal-pickup-address').fill('Unsaved modal draft must survive server failure');
        release(); await visibleError('Weight must be non-negative');
        assert.ok((await page.locator('#status-msg').textContent()).includes('Save failed.'));
        assert.ok((await page.locator('#status-msg').textContent()).includes('Weight must be non-negative'));
        assert.equal(await page.locator('#modal-pickup-address').inputValue(),'Unsaved modal draft must survive server failure');
        assert.ok((await page.locator('#sheet-body .row-error').textContent()).includes('Weight'));
        await page.screenshot({path:path.join(artifacts,'shipment-server-error-desktop.png')});
        await page.unroute('**/admin/consignments/save');
        await modal.getByRole('button',{name:'Cancel',exact:true}).click(); await modal.waitFor({state:'hidden'});
        const saved=page.waitForResponse(response=>response.url().endsWith('/consignments/save'));
        await page.locator('#save-btn').click(); assert.equal((await saved).status(),200);
        await page.waitForFunction(()=>!document.querySelector('#save-btn').disabled);
        assert.ok((await page.locator('#status-msg').textContent()).includes('Saved successfully'));
        console.log('PASS: delayed server validation is surfaced in the open modal with its actual error; correction/save flow still succeeds and page success stays on the page.');

        await page.goto(base+'/admin/companies'); await page.locator('#add-company-btn').click();
        await page.locator('#companyModal').waitFor({state:'visible'}); await page.locator('#company-name').fill('System Test Client');
        for(let i=0;i<5;i++) {
            await page.locator('.add-location[data-kind=pickup]').click();
            const item=page.locator('#company-pickup-locations .location-editor').last();
            await item.locator('.location-label').fill('Warehouse '+i); await item.locator('.location-address').fill('Warehouse address '+i);
        }
        await page.locator('#company-save-btn').click();
        const companyError=page.locator('#company-form-error'); await companyError.waitFor({state:'visible'});
        assert.ok((await companyError.textContent()).includes('already exists'));
        assert.equal(await companyError.evaluate(element=>{const r=element.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight;}),true);
        assert.equal(await page.locator('#company-name').inputValue(),'System Test Client');
        assert.equal(await page.locator('.location-editor').count(),5);
        console.log('PASS: company server validation scrolls into view inside its modal without losing entered locations.');

        await page.goto(base+'/admin/consignments'); await page.locator('[data-bs-target="#bulkDocumentsModal"]').click();
        await page.locator('#bulkDocumentsModal').waitFor({state:'visible'}); await page.locator('#bulk-download-btn').scrollIntoViewIfNeeded();
        await page.locator('#bulk-review-btn').click(); const bulk=page.locator('#bulk-documents-feedback'); await bulk.waitFor({state:'visible'});
        assert.ok((await bulk.textContent()).includes('Choose 1–50 files'));
        assert.equal(await bulk.evaluate(element=>{const r=element.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight;}),true);
        assert.equal(await bulk.getAttribute('role'),'alert');
        console.log('PASS: bulk-upload errors are visible and announced inside the dialog; no JavaScript errors.');
        await page.setViewportSize({width:390,height:550});
        await page.goto(base+'/admin/documents');
        await page.getByRole('button',{name:/^Upload POD for/}).first().click();
        await page.locator('#documentEditorModal').waitFor({state:'visible'});
        await page.locator('#document-editor-file').scrollIntoViewIfNeeded();
        await page.locator('#document-editor-file').setInputFiles({name:'unsafe.svg',mimeType:'image/svg+xml',buffer:Buffer.from('<svg/>')});
        const documentError=page.locator('#document-editor-error'); await documentError.waitFor({state:'visible'});
        await page.waitForFunction(()=>getComputedStyle(document.getElementById('documentEditorModal')).opacity==='1');
        assert.equal(await documentError.evaluate(element=>{const r=element.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight;}),true);
        assert.equal(await page.locator('#document-editor-save').isDisabled(),true);
        await page.screenshot({path:path.join(artifacts,'document-error-small-screen.png')});
        assert.deepEqual(errors,[]);
        console.log('PASS: document-editor errors stay visible on a small screen and invalid uploads cannot be saved.');
        console.log('Artifacts: '+artifacts);
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
