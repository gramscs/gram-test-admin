/* Read report bytes before downloading, so login/error pages never become PDFs. */
(function () {
    'use strict';
    function filename(response) {
        const disposition = response.headers.get('Content-Disposition') || '';
        const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
        if (encoded) {
            try { return decodeURIComponent(encoded[1]); } catch (error) { /* use plain name */ }
        }
        const plain = disposition.match(/filename="([^"]+)"|filename=([^;]+)/i);
        return plain ? (plain[1] || plain[2]).trim() : 'shipment-mis.pdf';
    }
    async function download(url) {
        const response = await fetch(url, {credentials: 'same-origin', headers: {'Accept': 'application/pdf, application/octet-stream, application/json'}});
        const mime = (response.headers.get('Content-Type') || '').toLowerCase();
        if (mime.includes('text/html')) throw new Error('Your session may have expired. Log in again, then download the report.');
        if (!response.ok || mime.includes('application/json')) {
            const error = await response.json().catch(() => null);
            throw new Error(error?.message || error?.error || `The report could not be downloaded (HTTP ${response.status}). Please try again.`);
        }
        const content = await response.blob();
        const name = filename(response);
        if (!content.size) throw new Error('The report file is empty. Generate a new report and try again.');
        if (mime.includes('application/pdf') || name.toLowerCase().endsWith('.pdf')) {
            const signature = await content.slice(0,5).text();
            if (signature !== '%PDF-') throw new Error('The downloaded file is not a valid PDF. Generate a new report and try again.');
        }
        const objectUrl = URL.createObjectURL(content);
        const anchor = document.createElement('a');
        anchor.href = objectUrl; anchor.download = name; anchor.hidden = true;
        document.body.append(anchor); anchor.click(); anchor.remove();
        setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
    }
    function showError(message) {
        let feedback = document.getElementById('mis-feedback') || document.getElementById('report-download-error');
        if (!feedback) {
            feedback = document.createElement('div'); feedback.id = 'report-download-error';
            feedback.setAttribute('role','alert'); document.getElementById('main-content').prepend(feedback);
        }
        feedback.className = 'alert alert-danger'; feedback.textContent = message;
        feedback.scrollIntoView({block:'nearest',behavior:'smooth'});
    }
    document.addEventListener('click', async event => {
        const link = event.target.closest('a[data-report-download]');
        if (!link || event.defaultPrevented || event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        if (link.getAttribute('aria-busy') === 'true') return;
        link.setAttribute('aria-busy','true');
        try { await download(link.href); } catch (error) { showError(error.message); }
        finally { link.removeAttribute('aria-busy'); }
    });
    window.adminReportDownloads = {download};
})();
