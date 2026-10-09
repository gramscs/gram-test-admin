(function () {
    'use strict';
    var companies = [], currentRow = {}, select;
    function company(id) { return companies.find(function (row) { return String(row.id) === String(id); }); }
    function fill(side, location) {
        ['address', 'pincode', 'tag'].forEach(function (field) {
            document.getElementById('modal-' + side + '-' + field).value = location ? (field === 'tag' ? location.label : location[field]) : '';
        });
    }
    function locations(applyDefaults) {
        var selected = company(select.value);
        document.getElementById('company-presets').classList.toggle('d-none', !selected);
        ['pickup', 'drop'].forEach(function (side) {
            var preset = document.getElementById('modal-' + side + '-preset');
            preset.replaceChildren(new Option('Custom ' + side, ''));
            var choices = selected ? selected.locations.filter(function (row) { return row.kind === side; }) : [];
            choices.forEach(function (row) { preset.add(new Option(row.label + (row.is_default ? ' · default' : ''), row.id)); });
            var match = choices.find(function (row) {
                return ['address', 'pincode', 'tag'].every(function (field) {
                    return document.getElementById('modal-' + side + '-' + field).value === (field === 'tag' ? row.label : row[field]);
                });
            });
            if (applyDefaults && selected) {
                match = choices.find(function (row) { return row.is_default; }) || choices[0];
                fill(side, match);
            }
            preset.value = match ? String(match.id) : '';
        });
    }
    function setRow(row) {
        currentRow = row;
        if (!select) return;
        if (row.company_id && !Array.from(select.options).some(option => option.value === String(row.company_id))) select.add(new Option(row.company_name || 'Existing client', row.company_id));
        select.value = row.company_id ? String(row.company_id) : '';
        locations(false);
    }
    window.companyPresets = {setRow: setRow, companyName: function (id) { var row = company(id); return row ? row.name : (String(currentRow.company_id) === String(id) ? currentRow.company_name || '' : ''); }};
    document.addEventListener('DOMContentLoaded', async function () {
        select = document.getElementById('modal-company-id');
        if (!select) return;
        select.addEventListener('change', function () {
            currentRow = Object.assign({}, currentRow, {company_id: select.value || null, company_name: window.companyPresets.companyName(select.value)});
            locations(true);
        });
        ['pickup', 'drop'].forEach(function (side) {
            document.getElementById('modal-' + side + '-preset').addEventListener('change', function (event) {
                var selected = company(select.value);
                var location = selected && selected.locations.find(function (row) { return String(row.id) === event.target.value && row.kind === side; });
                if (location) fill(side, location);
            });
            ['address', 'pincode', 'tag'].forEach(function (field) {
                document.getElementById('modal-' + side + '-' + field).addEventListener('input', function () {
                    document.getElementById('modal-' + side + '-preset').value = '';
                });
            });
        });
        try {
            var response = await fetch(select.dataset.optionsUrl, {headers: {'Accept': 'application/json'}});
            var data = await response.json();
            if (!response.ok || !data.success) throw new Error('Unable to load client masters. Check the connection and database upgrade.');
            companies = data.companies;
            select.replaceChildren(new Option('No client selected', ''));
            companies.forEach(function (row) { var option = new Option(row.name + (row.active ? '' : ' · archived'), row.id); option.disabled = !row.active; select.add(option); });
            setRow(currentRow);
        } catch (error) {
            var alert = document.getElementById('company-options-error');
            alert.textContent = error.message;
            alert.classList.remove('d-none');
            // Retain the existing link if masters cannot load; editing other fields must not remove it.
            setRow(currentRow);
        }
    });
})();
