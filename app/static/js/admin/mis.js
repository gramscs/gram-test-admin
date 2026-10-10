(function () {
    'use strict';
    const form=document.getElementById('mis-builder');
    const config=JSON.parse(document.getElementById('mis-config').textContent);
    let loaded=null, busy=false, editing=null;
    const feedback=document.getElementById('mis-feedback');
    function show(message,error=false) {
        feedback.textContent=message;feedback.className=`alert ${error?'alert-danger':'alert-success'} mis-feedback`;
        feedback.scrollIntoView({block:'nearest',behavior:'smooth'});
    }
    const notice=sessionStorage.getItem('mis-notice');
    if(notice){sessionStorage.removeItem('mis-notice');show(notice);}
    function value(name){return form.elements.namedItem(name).value;}
    function content(){return {name:value('name'),notes:value('notes'),format:value('format'),filters:{period:value('period'),start:value('start'),end:value('end'),company:value('company'),status:value('status'),search:value('search')},columns:Array.from(form.querySelectorAll('[name="columns"]:checked')).map(input=>input.value)};}
    async function action(work){
        if(busy)return;
        busy=true;form.querySelectorAll('button').forEach(button=>button.disabled=true);
        try{await work();}catch(error){show(error.message,true);}finally{busy=false;form.querySelectorAll('button').forEach(button=>button.disabled=false);}
    }
    function reload(message){sessionStorage.setItem('mis-notice',message);window.location.reload();}
    form.addEventListener('submit',event=>{
        event.preventDefault();
        action(async()=>{
            show('Generating your report. Keep this page open…');
            const result=await adminUI.request(config.report_url,content());
            const link=document.createElement('a');link.href=result.download_url;link.setAttribute('data-report-download','');link.textContent='Download generated report';
            show('Report saved. Downloading your file…');
            await adminReportDownloads.download(result.download_url);
            show('Report saved. Your download is ready. ');feedback.append(link);
            // Avoid interrupting the download with a page reload.
            const refresh=document.createElement('button');refresh.type='button';refresh.className='btn btn-sm btn-outline-secondary ms-2';refresh.textContent='Refresh report history';refresh.addEventListener('click',()=>reload('Report history updated.'));feedback.append(refresh);
        });
    });
    document.getElementById('save-view').addEventListener('click',()=>{
        if(!form.reportValidity())return;
        action(async()=>{await adminUI.request(config.view_url,content());reload('Saved view added.');});
    });
    document.getElementById('update-view').addEventListener('click',()=>{
        if(!loaded||!form.reportValidity())return;
        action(async()=>{await adminUI.request(`${config.view_url}/${loaded}`,content(),'PUT');reload('Saved view updated.');});
    });
    document.querySelectorAll('[data-load-view]').forEach(button=>button.addEventListener('click',()=>{
        const view=config.views.find(row=>row.id===Number(button.dataset.loadView));loaded=view.id;
        form.elements.namedItem('search').value=view.filters.search||'';
        Object.entries(view.filters).forEach(([key,val])=>{form.elements.namedItem(key).value=val;});
        for(const key of ['name','notes','format'])form.elements.namedItem(key).value=view[key];
        form.querySelectorAll('[name="columns"]').forEach(input=>input.checked=view.columns.includes(input.value));
        document.getElementById('report-period').dispatchEvent(new Event('change'));
        document.getElementById('loaded-view').textContent=`Editing saved view: ${view.name}`;
        document.getElementById('update-view').hidden=false;
        form.scrollIntoView({block:'start',behavior:'smooth'});
    }));
    document.querySelectorAll('[data-delete-view]').forEach(button=>button.addEventListener('click',()=>{
        if(!confirm(`Delete saved view “${button.dataset.viewName}”? Generated reports will stay in history.`))return;
        action(async()=>{await adminUI.request(`${config.view_url}/${button.dataset.deleteView}`,{},'DELETE');reload('Saved view deleted.');});
    }));
    document.querySelectorAll('[data-delete-report]').forEach(button=>button.addEventListener('click',()=>{
        if(!confirm(`Delete report “${button.dataset.reportName}” and its stored file? Shipment records will remain unchanged.`))return;
        action(async()=>{await adminUI.request(`${config.report_url}/${button.dataset.deleteReport}`,{},'DELETE');reload('Report deleted.');});
    }));
    const modal=new bootstrap.Modal(document.getElementById('report-edit-modal'));
    document.querySelectorAll('[data-edit-report]').forEach(button=>button.addEventListener('click',()=>{
        editing=config.reports.find(row=>row.id===Number(button.dataset.editReport));
        document.getElementById('report-edit-name').value=editing.name;
        document.getElementById('report-edit-notes').value=editing.notes;
        adminUI.alert('report-edit-error','');modal.show();
    }));
    const editForm=document.getElementById('report-edit-form');
    editForm.addEventListener('submit',async event=>{
        event.preventDefault();const button=editForm.querySelector('[type="submit"]');button.disabled=true;
        try{await adminUI.request(`${config.report_url}/${editing.id}`,{name:document.getElementById('report-edit-name').value,notes:document.getElementById('report-edit-notes').value},'PUT');reload('Report details updated.');}
        catch(error){adminUI.alert('report-edit-error',error.message);}
        finally{button.disabled=false;}
    });
})();
