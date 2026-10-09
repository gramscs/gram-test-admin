document.addEventListener('DOMContentLoaded', function () {
    const inputs = Array.from(document.querySelectorAll('[name="consignment_ids"]'));
    const all = document.getElementById('select-all-labels');
    function update() {
        const selected = inputs.filter(input => input.checked);
        const pieces = selected.reduce((total, input) => total + Number(input.dataset.pieces), 0);
        document.getElementById('label-selected-count').textContent = selected.length;
        document.getElementById('label-piece-count').textContent = pieces;
        document.getElementById('label-limit-error').classList.toggle('d-none', pieces <= 500);
        ['preview-labels-btn', 'download-labels-btn'].forEach(id => document.getElementById(id).disabled = !pieces || pieces > 500);
        all.checked = selected.length > 0 && selected.length === inputs.length;
        all.indeterminate = selected.length > 0 && selected.length < inputs.length;
    }
    all.addEventListener('change', function () { inputs.forEach(input => input.checked = this.checked); update(); });
    inputs.forEach(input => input.addEventListener('change', update));
    document.getElementById('include-label-barcode').addEventListener('change', function () { document.getElementById('label-barcode-value').value = this.checked ? '1' : '0'; });
    update();
});
