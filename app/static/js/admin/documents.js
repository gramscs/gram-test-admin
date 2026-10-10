(function () {
    'use strict';
    const kinds = ['pod', 'invoice'];
    const fields = {pod: 'pod_image', invoice: 'invoice_file'};
    const state = {};
    let shipmentId = null;
    const element = (kind, part) => document.getElementById('modal-' + kind + '-' + part);
    function revoke(item) {
        if (item && item.previewURL) URL.revokeObjectURL(item.previewURL);
    }
    function dataBlob(upload) {
        const raw = atob(upload.dataUrl.split(',')[1]);
        return new Blob([Uint8Array.from(raw, char => char.charCodeAt(0))], {type: upload.type});
    }
    function render(kind) {
        const item = state[kind];
        if (!item || !element(kind, 'file')) return;
        revoke(item);
        item.previewURL = null;
        const attached = !item.removed && (item.upload || item.reference);
        element(kind, 'status').textContent = item.removed ? 'Removal staged. Save All will remove this document.' : item.upload ? 'Selected: ' + item.upload.name + ' · awaiting Save All' : item.reference ? 'Attached: ' + item.name : 'No document attached.';
        element(kind, 'error').textContent = item.error;
        element(kind, 'download').classList.toggle('d-none', !attached);
        element(kind, 'remove').classList.toggle('d-none', !attached);
        element(kind, 'undo').classList.toggle('d-none', !item.changed && !item.error);
        const image = element(kind, 'image');
        image.classList.add('d-none');
        image.removeAttribute('src');
        if (attached) {
            if (item.upload && ['image/jpeg', 'image/png', 'image/webp'].includes(item.upload.type)) {
                item.previewURL = URL.createObjectURL(item.upload.file || dataBlob(item.upload));
                image.src = item.previewURL;
                image.classList.remove('d-none');
            } else if (!item.upload && /\.(png|jpe?g|webp)$/i.test(item.reference)) {
                image.src = '/admin/consignments/' + shipmentId + '/' + kind + '?preview=1';
                image.classList.remove('d-none');
            }
        }
    }
    function setRow(row) {
        shipmentId = row.id && !String(row.id).startsWith('-') ? row.id : null;
        kinds.forEach(kind => {
            revoke(state[kind]);
            const upload = row[kind + '_file_data'] ? {name: row[kind + '_file_name'], type: row[kind + '_file_type'], dataUrl: row[kind + '_file_data']} : null;
            const reference = row[fields[kind]] || null;
            const base = {reference, name: row[kind + '_original_name'] || (reference ? reference.split('/').pop() : ''), upload, removed: !!row[kind + '_remove']};
            state[kind] = {...base, base, changed: false, error: '', sequence: 0, pending: null};
            if (element(kind, 'file')) element(kind, 'file').value = '';
            render(kind);
        });
    }
    async function selectFile(kind, file) {
        const item = state[kind];
        const sequence = ++item.sequence;
        item.error = '';
        if (!file) return;
        try {
            if (!file.size || file.size > 5 * 1024 * 1024) throw new Error('Choose a non-empty file of at most 5 MB.');
            const extension = file.name.split('.').pop().toLowerCase();
            if (!['pdf', 'jpg', 'jpeg', 'png', 'webp'].includes(extension)) throw new Error('Use PDF, JPG, PNG or WebP.');
            const bytes = new Uint8Array(await file.slice(0, 16).arrayBuffer());
            const text = String.fromCharCode(...bytes);
            const mime = text.startsWith('%PDF-') ? 'application/pdf' : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 ? 'image/jpeg' : bytes[0] === 137 && text.slice(1, 4) === 'PNG' ? 'image/png' : text.startsWith('RIFF') && text.slice(8, 12) === 'WEBP' ? 'image/webp' : '';
            const expected = {pdf: 'application/pdf', jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp'};
            if (!mime || mime !== expected[extension]) throw new Error('The filename does not match the document contents.');
            const dataUrl = await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result));
                reader.onerror = () => reject(new Error('Unable to read this file. Choose it again.'));
                reader.readAsDataURL(new Blob([file], {type: mime}));
            });
            if (state[kind] !== item || sequence !== item.sequence) return;
            item.upload = {name: file.name, type: mime, dataUrl, file};
            item.removed = false;
            item.changed = true;
        } catch (error) {
            if (state[kind] !== item || sequence !== item.sequence) return;
            item.error = error.message;
        }
        if (state[kind] === item && sequence === item.sequence) render(kind);
    }
    document.addEventListener('DOMContentLoaded', function () {
        kinds.forEach(kind => {
            if (!element(kind, 'file')) return;
            element(kind, 'file').addEventListener('change', () => {
                state[kind].pending = selectFile(kind, element(kind, 'file').files[0]);
            });
            element(kind, 'remove').addEventListener('click', () => {
                const item = state[kind];
                item.sequence++;
                item.upload = null;
                item.removed = true;
                item.changed = true;
                item.error = '';
                element(kind, 'file').value = '';
                render(kind);
            });
            element(kind, 'undo').addEventListener('click', () => {
                const item = state[kind];
                item.sequence++;
                Object.assign(item, item.base, {changed: false, error: ''});
                element(kind, 'file').value = '';
                render(kind);
            });
            element(kind, 'image').addEventListener('error', () => element(kind, 'image').classList.add('d-none'));
            element(kind, 'download').addEventListener('click', () => {
                const item = state[kind];
                if (item.removed) return;
                const link = document.createElement('a');
                let url;
                if (item.upload) {
                    url = URL.createObjectURL(item.upload.file || dataBlob(item.upload));
                    link.href = url;
                    link.download = item.upload.name;
                } else {
                    link.href = '/admin/consignments/' + shipmentId + '/' + kind;
                }
                document.body.appendChild(link);
                link.click();
                link.remove();
                if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
            });
        });
        setRow({});
    });
    window.shipmentDocuments = {
        setRow,
        reset: () => setRow({}),
        ready: async function () {
            await Promise.all(kinds.map(kind => state[kind].pending));
            for (const kind of kinds) {
                if (state[kind].error) {
                    element(kind, 'file').focus();
                    throw new Error(state[kind].error);
                }
            }
        },
        apply: function (row) {
            kinds.forEach(kind => {
                const item = state[kind];
                row[fields[kind]] = item.reference;
                row[kind + '_original_name'] = item.name || null;
                row[kind + '_remove'] = item.removed;
                row[kind + '_file_name'] = item.upload ? item.upload.name : null;
                row[kind + '_file_type'] = item.upload ? item.upload.type : null;
                row[kind + '_file_data'] = item.upload ? item.upload.dataUrl : null;
            });
        }
    };
})();
