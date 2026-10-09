document.addEventListener('DOMContentLoaded', function () {
    const rows = Array.from(document.querySelectorAll('[data-lead]'));
    const leadModal = new bootstrap.Modal(document.getElementById('leadModal'));
    const emailModal = new bootstrap.Modal(document.getElementById('enquiryEmailModal'));
    const form = document.getElementById('lead-form'), emailForm = document.getElementById('enquiry-email-form');
    let editingId = null, emailIds = [], previewSequence = 0;
    const selected = () => Array.from(document.querySelectorAll('.lead-select:checked')).map(input => Number(input.value));
    function updateSelection() {
        const count = selected().length, all = document.getElementById('select-all-leads');
        document.getElementById('lead-selected-count').textContent = count;
        ['email-leads-btn', 'export-leads-btn', 'update-leads-btn', 'delete-leads-btn'].forEach(id => document.getElementById(id).disabled = !count);
        all.checked = count > 0 && count === rows.length; all.indeterminate = count > 0 && count < rows.length;
    }
    document.getElementById('select-all-leads').addEventListener('change', function () { document.querySelectorAll('.lead-select').forEach(input => input.checked = this.checked); updateSelection(); });
    document.querySelectorAll('.lead-select').forEach(input => input.addEventListener('change', updateSelection));
    function openLead(lead) {
        editingId = lead?.id || null; form.reset(); adminUI.alert('lead-form-error', '');
        document.getElementById('lead-modal-title').textContent = editingId ? 'Edit Enquiry' : 'Add Enquiry';
        ['name', 'email', 'phone', 'company_name', 'subject', 'message', 'notes', 'follow_up_date'].forEach(key => document.getElementById('lead-' + key).value = lead?.[key] || '');
        document.getElementById('lead-status').value = lead?.status || 'New'; leadModal.show();
    }
    document.getElementById('add-lead-btn').addEventListener('click', () => openLead(null));
    document.getElementById('leadModal').addEventListener('shown.bs.modal', () => document.getElementById('lead-name').focus());
    async function bulk(ids, action, status) {
        try { await adminUI.request('/admin/leads/bulk', {ids, action, status}); location.reload(); }
        catch (error) { adminUI.alert('leads-error', error.message); }
    }
    rows.forEach(row => {
        const lead = JSON.parse(row.dataset.lead);
        row.querySelector('.edit-lead').addEventListener('click', () => openLead(lead));
        row.querySelector('.email-lead').addEventListener('click', () => openEmail([lead.id]));
        row.querySelector('.delete-lead').addEventListener('click', () => { if (confirm('Delete this enquiry from ' + lead.name + '? This cannot be undone.')) bulk([lead.id], 'delete'); });
    });
    document.getElementById('delete-leads-btn').addEventListener('click', () => { const ids = selected(); if (confirm('Delete ' + ids.length + ' selected enquiries? This cannot be undone.')) bulk(ids, 'delete'); });
    document.getElementById('update-leads-btn').addEventListener('click', () => {
        const status = document.getElementById('bulk-lead-status').value;
        if (!status) { adminUI.alert('leads-error', 'Choose a status first.'); return; }
        bulk(selected(), 'status', status);
    });
    document.getElementById('export-leads-btn').addEventListener('click', () => { const params = new URLSearchParams(); selected().forEach(id => params.append('ids', id)); location.href = '/admin/leads/export.xlsx?' + params; });
    form.addEventListener('submit', async event => {
        event.preventDefault(); const button = document.getElementById('lead-save-btn'); button.disabled = true;
        try { await adminUI.request('/admin/leads' + (editingId ? '/' + editingId : ''), Object.fromEntries(new FormData(form)), editingId ? 'PUT' : 'POST'); location.reload(); }
        catch (error) { adminUI.alert('lead-form-error', error.message); button.disabled = false; }
    });
    async function preview() {
        const sequence = ++previewSequence;
        try {
            const result = await adminUI.request('/admin/leads/email-preview', {ids: emailIds, include_notes: document.getElementById('email-include-notes').checked});
            if (sequence !== previewSequence) return;
            document.getElementById('enquiry-email-subject').value = result.subject;
            document.getElementById('enquiry-email-body').value = result.body; updateEmailLength();
        } catch (error) { adminUI.alert('email-form-error', error.message); }
    }
    function openEmail(ids) {
        emailIds = ids; emailForm.reset(); adminUI.alert('email-form-error', ''); emailModal.show(); preview();
    }
    document.getElementById('email-leads-btn').addEventListener('click', () => openEmail(selected()));
    document.getElementById('email-include-notes').addEventListener('change', () => {
        if (confirm('Rebuild the message preview with this notes setting? Any edits to the subject or message will be replaced.')) preview();
        else document.getElementById('email-include-notes').checked = !document.getElementById('email-include-notes').checked;
    });
    function fields() { return {to: document.getElementById('enquiry-email-to').value.trim(), subject: document.getElementById('enquiry-email-subject').value, body: document.getElementById('enquiry-email-body').value}; }
    function mailto() { const data = fields(); return 'mailto:' + encodeURIComponent(data.to).replaceAll('%2C', ',') + '?subject=' + encodeURIComponent(data.subject) + '&body=' + encodeURIComponent(data.body); }
    function updateEmailLength() {
        const long = mailto().length > 1800;
        document.getElementById('open-email-app').disabled = long;
        document.getElementById('email-length-hint').textContent = long ? 'This draft is too long for a reliable email-app link. Download the complete .eml draft and open/import it in your email app.' : 'Your email app opens a draft. You review and send it there.';
    }
    ['to', 'subject', 'body'].forEach(key => document.getElementById('enquiry-email-' + key).addEventListener('input', updateEmailLength));
    function validEmail() {
        if (!emailForm.reportValidity()) return false;
        const recipients = fields().to.split(',').map(value => value.trim());
        if (recipients.length > 10 || recipients.some(value => value.length > 254 || value.includes('..') || !/^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$/.test(value))) {
            adminUI.alert('email-form-error', 'Enter up to ten valid recipient email addresses, separated by commas.'); return false;
        }
        adminUI.alert('email-form-error', ''); return true;
    }
    emailForm.addEventListener('submit', event => { event.preventDefault(); if (validEmail() && mailto().length <= 1800) location.href = mailto(); });
    document.getElementById('download-email-draft').addEventListener('click', () => { if (validEmail()) adminUI.downloadForm('/admin/leads/email-draft', {...fields(), ids: emailIds}); });
});
