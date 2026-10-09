document.addEventListener("DOMContentLoaded", function () {
    var tableBody = document.getElementById("sheet-body");
    var saveButton = document.getElementById("save-btn");
    var addRowButton = document.getElementById("add-row-btn");
    var editModal = new bootstrap.Modal(document.getElementById("editConsignmentModal"));
    var modalSaveBtn = document.getElementById("modal-save-btn");
    var searchInput = document.getElementById("search-input");
    var perPageSelect = document.getElementById("per-page-select");
    var clearFiltersBtn = document.getElementById("clear-filters-btn");
    var prevPageBtn = document.getElementById("prev-page-btn");
    var nextPageBtn = document.getElementById("next-page-btn");
    var pageNumbersContainer = document.getElementById("page-numbers-container");

    if (!tableBody || !saveButton || !addRowButton) {
        return;
    }

    var saveUrl = tableBody.dataset.saveUrl || "";
    var listUrl = tableBody.dataset.listUrl || "";
    var currentEditingRow = null;
    var isCreatingRow = false;
    var searchTimeout;
    var currentPage = 1;
    var currentPerPage = perPageSelect ? (parseInt(perPageSelect.value, 10) || 10) : 10;
    var currentSearch = "";
    var currentSortBy = "id";
    var currentSortOrder = "asc";
    var totalRows = 0;
    var totalPages = 1;
    var statusTimeoutId = null;

    function buildStatusSelect(value) {
        var options = [
            "",
            "Pickup Scheduled",
            "In Transit",
            "Out for Delivery",
            "Delivered"
        ];
        var html = '<select aria-label="Status" class="form-select form-select-sm status">';
        options.forEach(function (option) {
            var selected = option === (value || "") ? ' selected' : '';
            html += '<option value="' + escapeHtml(option) + '"' + selected + '>' + (option ? escapeHtml(option) : 'Select status') + '</option>';
        });
        html += '</select>';
        return html;
    }

    function buildTextInput(className, value, placeholder, maxlength) {
        var attrs = [
            'class="form-control form-control-sm ' + className + '"',
            'aria-label="' + escapeHtml(placeholder || className.replaceAll('_', ' ')) + '"',
            'value="' + escapeHtml(value || "") + '"'
        ];
        if (placeholder) {
            attrs.push('placeholder="' + escapeHtml(placeholder) + '"');
        }
        if (maxlength) {
            attrs.push('maxlength="' + maxlength + '"');
        }
        return '<input type="text" ' + attrs.join(' ') + ' />';
    }

    function buildIdentifierSelect(value) {
        return '<select aria-label="Identifier type" class="form-select form-select-sm identifier_type">' + ['LRN', 'Order ID', 'AWB'].map(function (type) {
            return '<option' + (type === (value || 'LRN') ? ' selected' : '') + '>' + type + '</option>';
        }).join('') + '</select>';
    }
    function buildNumberInput(name, value, min, step) {
        return '<input type="number" aria-label="' + name.replaceAll('_', ' ') + '" class="form-control form-control-sm ' + name + '" min="' + min + '" step="' + step + '" value="' + escapeHtml(String(value)) + '" />';
    }

    function syncRowDataset(tr) {
        if (!tr) {
            return null;
        }

        var baseRow = {};
        try {
            baseRow = JSON.parse(tr.dataset.row || "{}") || {};
        } catch (e) {
            baseRow = {};
        }

        var row = Object.assign({}, baseRow, {
            id: tr.dataset.id ? Number(tr.dataset.id) : (baseRow.id || null),
            consignment_number: tr.querySelector('.consignment_number') ? tr.querySelector('.consignment_number').value.trim() : (baseRow.consignment_number || ''),
            identifier_type: tr.querySelector('.identifier_type') ? tr.querySelector('.identifier_type').value : (baseRow.identifier_type || 'LRN'),
            pieces: tr.querySelector('.pieces') ? tr.querySelector('.pieces').value : (baseRow.pieces || 1),
            chargeable_weight: tr.querySelector('.chargeable_weight') ? tr.querySelector('.chargeable_weight').value : (baseRow.chargeable_weight ?? ''),
            chargeable_volume: tr.querySelector('.chargeable_volume') ? tr.querySelector('.chargeable_volume').value : (baseRow.chargeable_volume ?? ''),
            status: tr.querySelector('.status') ? tr.querySelector('.status').value.trim() : (baseRow.status || ''),
            pickup_address: tr.querySelector('.pickup_address') ? tr.querySelector('.pickup_address').value.trim() : (baseRow.pickup_address || ''),
            pickup_pincode: tr.querySelector('.pickup_pincode') ? tr.querySelector('.pickup_pincode').value.trim() : (baseRow.pickup_pincode || ''),
            pickup_tag: tr.querySelector('.pickup_tag') ? tr.querySelector('.pickup_tag').value.trim() : (baseRow.pickup_tag || ''),
            pickup_date: tr.querySelector('.pickup_date') ? tr.querySelector('.pickup_date').value.trim() : (baseRow.pickup_date || ''),
            drop_address: tr.querySelector('.drop_address') ? tr.querySelector('.drop_address').value.trim() : (baseRow.drop_address || ''),
            drop_pincode: tr.querySelector('.drop_pincode') ? tr.querySelector('.drop_pincode').value.trim() : (baseRow.drop_pincode || ''),
            drop_tag: tr.querySelector('.drop_tag') ? tr.querySelector('.drop_tag').value.trim() : (baseRow.drop_tag || ''),
            drop_date: tr.querySelector('.drop_date') ? tr.querySelector('.drop_date').value.trim() : (baseRow.drop_date || ''),
            eta: tr.querySelector('.eta') ? tr.querySelector('.eta').value.trim() : (baseRow.eta || ''),

        });
        tr.dataset.row = JSON.stringify(row);
        return row;
    }

    function showStatus(message, type) {
        var el = document.getElementById("status-msg");
        if (!el) {
            return;
        }
        el.innerHTML = message;
        el.className = "alert alert-" + type + " shadow-sm border-0";
        el.classList.remove("d-none");
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        // Auto-dismiss status messages after 10 seconds
        try {
            if (statusTimeoutId) {
                clearTimeout(statusTimeoutId);
            }
            statusTimeoutId = setTimeout(function () {
                try {
                    el.classList.add('d-none');
                } catch (e) {}
            }, 10000);
        } catch (e) {}
    }

    function escapeHtml(text) {
        return String(text == null ? "" : text)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function populateModal(row) {
        var consInput = document.getElementById("modal-consignment-number");
        consInput.value = row.consignment_number || "";
        ['identifier_type', 'pieces', 'chargeable_weight', 'chargeable_volume'].forEach(function (name) {
            document.getElementById('modal-' + name.replaceAll('_', '-')).value = row[name] ?? (name === 'identifier_type' ? 'LRN' : name === 'pieces' ? 1 : '');
        });
        // Ensure the input is editable (some scripts may toggle readOnly)
        try {
            consInput.readOnly = false;
        } catch (e) {
            // ignore
        }
        consInput.focus();
        document.getElementById("modal-status").value = row.status || "";
        document.getElementById("modal-pickup-address").value = row.pickup_address || "";
        document.getElementById("modal-pickup-pincode").value = row.pickup_pincode || "";
        document.getElementById("modal-pickup-tag").value = row.pickup_tag || "";
        document.getElementById("modal-pickup-date").value = row.pickup_date || "";
        document.getElementById("modal-drop-address").value = row.drop_address || "";
        document.getElementById("modal-drop-pincode").value = row.drop_pincode || "";
        document.getElementById("modal-drop-tag").value = row.drop_tag || "";
        document.getElementById("modal-drop-date").value = row.drop_date || "";
        window.companyPresets.setRow(row);
        window.shipmentDocuments.setRow(row);
    }

    function clearModal() {
        window.shipmentDocuments.reset();
        populateModal({
            consignment_number: "",
            status: "",
            pickup_address: "",
            pickup_pincode: "",
            pickup_tag: "",
            pickup_date: "",
            drop_address: "",
            drop_pincode: "",
            drop_tag: "",
            drop_date: "",
            eta: "",
            pod_image: null,
            pod_file_name: null,
            pod_file_type: null,
            pod_file_data: null
        });
    }

    function buildRowData(source, fallbackId) {
        var data = source || {};
        return {
            id: data.id || fallbackId || null,
            company_id: data.company_id ?? null, company_name: data.company_name || '',
            consignment_number: data.consignment_number || "",
            identifier_type: data.identifier_type || "LRN", pieces: data.pieces ?? 1,
            chargeable_weight: data.chargeable_weight ?? "", chargeable_volume: data.chargeable_volume ?? "",
            status: data.status || "",
            pickup_address: data.pickup_address || "",
            pickup_pincode: data.pickup_pincode || "",
            pickup_tag: data.pickup_tag || "",
            pickup_date: data.pickup_date || "",
            drop_address: data.drop_address || "",
            drop_pincode: data.drop_pincode || "",
            drop_tag: data.drop_tag || "",
            drop_date: data.drop_date || "",
            eta: data.eta || "",
            pod_image: data.pod_image || null,
            pod_original_name: data.pod_original_name || null,
            pod_remove: !!data.pod_remove,
            invoice_file: data.invoice_file || null,
            invoice_original_name: data.invoice_original_name || null,
            invoice_file_name: data.invoice_file_name || null,
            invoice_file_type: data.invoice_file_type || null,
            invoice_file_data: data.invoice_file_data || null,
            invoice_remove: !!data.invoice_remove,
            pod_file_name: data.pod_file_name || null,
            pod_file_type: data.pod_file_type || null,
            pod_file_data: data.pod_file_data || null
        };
    }

    function getRowDataFromTr(tr) {
        try {
            if (tr && tr.querySelector('.consignment_number')) {
                return buildRowData(syncRowDataset(tr), tr.dataset.id ? Number(tr.dataset.id) : null);
            }
            return buildRowData(JSON.parse(tr.dataset.row || "{}"), tr.dataset.id ? Number(tr.dataset.id) : null);
        } catch (error) {
            return buildRowData({}, tr.dataset.id ? Number(tr.dataset.id) : null);
        }
    }

    function addRow(row, isLocal) {
        var source = buildRowData(row || {});
        var tr = document.createElement("tr");
        tr.dataset.id = source.id || "";
        tr.dataset.consignmentNumber = source.consignment_number || "";
        tr.dataset.row = JSON.stringify(source);
        tr.dataset.isLocal = isLocal ? "true" : "false";

        var rowClass = isLocal ? 'table-info' : '';

        tr.innerHTML =
            '<td><div class="cell-stack">' + buildIdentifierSelect(source.identifier_type) +
                buildTextInput("consignment_number", source.consignment_number || "", "Identifier", 64) +
                '<span class="client-name small text-muted">' + escapeHtml(source.company_name || '') + '</span></div></td>' +
            "<td>" + buildNumberInput('pieces', source.pieces ?? 1, 1, 1) + "</td>" +
            '<td><div class="cell-stack"><label class="unit-field">' + buildNumberInput('chargeable_weight', source.chargeable_weight ?? '', 0, '0.001') + '<span>kg</span></label>' +
                '<label class="unit-field">' + buildNumberInput('chargeable_volume', source.chargeable_volume ?? '', 0, '0.001') + '<span>m³</span></label></div></td>' +
            "<td>" + buildStatusSelect(source.status || "") + "</td>" +
            '<td><div class="cell-stack">' + buildTextInput("pickup_tag", source.pickup_tag || "", "Pickup tag") +
                buildTextInput("pickup_date", source.pickup_date || "", "Pickup date") + '</div></td>' +
            '<td><div class="cell-stack">' + buildTextInput("drop_pincode", source.drop_pincode || "", "Drop pin", 6) +
                buildTextInput("drop_date", source.drop_date || source.eta || "", "Drop estimated") + '</div></td>' +
            '<td><div class="d-flex justify-content-center gap-1"><button type="button" class="btn btn-sm btn-outline-primary edit-row" title="Edit" aria-label="Edit consignment"><i class="fa fa-pencil" aria-hidden="true"></i></button>' +
                '<button type="button" class="btn btn-sm btn-outline-danger delete-row" title="Delete" aria-label="Delete consignment"><i class="fa fa-times" aria-hidden="true"></i></button></div></td>';

        if (rowClass) {
            tr.className = rowClass;
        }

        var editButton = tr.querySelector(".edit-row");
        if (editButton) {
            editButton.addEventListener("click", function () {
                isCreatingRow = false;
                document.getElementById('editConsignmentLabel').textContent = 'Edit Consignment';
                currentEditingRow = tr;
                populateModal(getRowDataFromTr(tr));
                editModal.show();
            });
        }

        var deleteButton = tr.querySelector(".delete-row");
        if (deleteButton) {
            deleteButton.addEventListener("click", function () {
                var existingId = tr.dataset.id ? Number(tr.dataset.id) : null;
                if (existingId && existingId > 0) {
                    adminState.addDeleted(existingId);
                }
                // Remove from local tracking
                adminState.removeLocalRowById(existingId);
                tr.remove();
            });
        }

        Array.prototype.forEach.call(tr.querySelectorAll('input, select'), function (field) {
            field.addEventListener('input', function () { adminState.rememberRow(syncRowDataset(tr)); });
            field.addEventListener('change', function () { adminState.rememberRow(syncRowDataset(tr)); });
        });

        tableBody.appendChild(tr);
        syncRowDataset(tr);

    }

    function updateRowFromModal(tr, source) {
        var consignmentNumber = document.getElementById("modal-consignment-number").value.trim();
        var status = document.getElementById("modal-status").value.trim();
        var pickupPincode = document.getElementById("modal-pickup-pincode").value.trim();
        var dropPincode = document.getElementById("modal-drop-pincode").value.trim();

        if (!consignmentNumber) {
            showStatus("Consignment number cannot be empty.", "danger");
            return false;
        }

        if (!adminValidation.validatePincode(pickupPincode)) {
            showStatus("Pickup Pincode must be a valid 6-digit number or empty.", "danger");
            return false;
        }
        if (!adminValidation.validatePincode(dropPincode)) {
            showStatus("Drop Pincode must be a valid 6-digit number or empty.", "danger");
            return false;
        }

        source.consignment_number = consignmentNumber;
        source.company_id = document.getElementById('modal-company-id').value || null;
        source.company_name = window.companyPresets.companyName(source.company_id);
        ['identifier_type', 'pieces', 'chargeable_weight', 'chargeable_volume'].forEach(function (name) {
            source[name] = document.getElementById('modal-' + name.replaceAll('_', '-')).value;
        });
        source.status = status;
        source.pickup_address = document.getElementById("modal-pickup-address").value.trim();
        source.pickup_pincode = adminValidation.normalizePincode(pickupPincode);
        source.pickup_tag = document.getElementById("modal-pickup-tag").value.trim();
        source.pickup_date = document.getElementById("modal-pickup-date").value.trim();
        source.drop_address = document.getElementById("modal-drop-address").value.trim();
        source.drop_pincode = adminValidation.normalizePincode(dropPincode);
        source.drop_tag = document.getElementById("modal-drop-tag").value.trim();
        source.drop_date = document.getElementById("modal-drop-date").value.trim();
        window.shipmentDocuments.apply(source);

        if (tr) {
            ['identifier_type', 'pieces', 'chargeable_weight', 'chargeable_volume'].forEach(function (name) {
                var input = tr.querySelector('.' + name);
                if (input) input.value = source[name];
            });
            var consignmentInput = tr.querySelector('.consignment_number');
            if (consignmentInput) consignmentInput.value = source.consignment_number || "";
            tr.dataset.consignmentNumber = source.consignment_number || "";
            tr.querySelector('.client-name').textContent = source.company_name || '';
            var statusSelect = tr.querySelector('.status');
            if (statusSelect) statusSelect.value = source.status || "";
            tr.dataset.row = JSON.stringify(source);
            var pickupTagInput = tr.querySelector('.pickup_tag');
            if (pickupTagInput) pickupTagInput.value = source.pickup_tag || "";
            var dropPinInput = tr.querySelector('.drop_pincode');
            if (dropPinInput) dropPinInput.value = source.drop_pincode || "";
            var pickupDateInput = tr.querySelector('.pickup_date');
            if (pickupDateInput) pickupDateInput.value = source.pickup_date || "";
            var dropDateInput = tr.querySelector('.drop_date');
            if (dropDateInput) dropDateInput.value = source.drop_date || source.eta || "";

            adminState.rememberRow(source);
        }

        return true;
    }

    function collectRows() {
        var rows = [];
        var tableRows = document.querySelectorAll("#sheet-body tr");

        tableRows.forEach(function (tr) {
            var rowData = getRowDataFromTr(tr);
            if (rowData.consignment_number && rowData.consignment_number.trim()) {
                rows.push(rowData);
            }
        });

        return rows;
    }

    async function saveSheet() {
        if (!saveUrl) {
            showStatus("Save endpoint is missing.", "danger");
            return;
        }

        var rawRows = collectRows();
        adminState.pendingRows.forEach(function (row, id) {
            if (!adminState.deletedIds.has(id) && !rawRows.some(item => item.id === id)) rawRows.push(row);
        });
        // Include staged local rows that may not be present in DOM (user hasn't navigated to last page)
        try {
            var staged = (adminState && adminState.locallyAddedRows) ? adminState.locallyAddedRows : [];
            staged.forEach(function (s) {
                var exists = rawRows.some(function (r) { return r.id === s.id; });
                if (!exists) rawRows.push(s);
            });
        } catch (e) {}
        if (!rawRows.length && adminState.deletedIds.size === 0) {
            showStatus("No changes to save.", "warning");
            return;
        }

        try {
            saveButton.disabled = true;
            var originalButtonText = saveButton.textContent;
            saveButton.textContent = "Saving...";
            showStatus('<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Saving rows to database...', "info");

            // Delegate network call to adminAPI
            var data = await adminAPI.saveRows(saveUrl, {
                rows: rawRows,
                deleted_ids: Array.from(adminState.deletedIds)
            });

            // Handle structured per-row validation errors from the server
            if (data && Array.isArray(data.errors) && data.errors.length) {
                // Clear any previous row error markers
                document.querySelectorAll('#sheet-body tr .row-error').forEach(function (el) { el.remove(); });
                var trs = Array.from(document.querySelectorAll('#sheet-body tr'));
                var firstTr = null;
                data.errors.forEach(function (err) {
                    var idx = err.index || 0;
                    var msg = err.message || 'Invalid value';
                    var tr = trs[idx];
                    if (!tr) return;
                    firstTr = firstTr || tr;
                    tr.classList.add('table-danger');
                    // insert or update an inline error element
                    var existing = tr.querySelector('.row-error');
                    if (existing) {
                        existing.textContent = msg;
                    } else {
                        var td = document.createElement('td');
                        td.colSpan = tr.cells.length;
                        td.className = 'row-error text-danger small';
                        td.textContent = msg;
                        var erTr = document.createElement('tr');
                        erTr.className = 'row-error-row';
                        erTr.appendChild(td);
                        tr.parentNode.insertBefore(erTr, tr.nextSibling);
                    }
                });

                if (firstTr) {
                    firstTr.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }

                throw new Error('Validation errors. Please fix highlighted rows.');
            }

            if (!data || !data.success) {
                throw new Error((data && data.message) || "Save failed.");
            }

            showStatus("<strong>Saved successfully.</strong> Your internal database has been updated.", "success");
            adminState.resetAfterSave();
            setTimeout(function () {
                // Prefer server-provided total (after commit) to compute the
                // page that will contain newly inserted rows. Fall back to
                // an estimate using the locally tracked counts.
                try {
                    var totalAfter = (data && typeof data.total === 'number')
                        ? data.total
                        : (totalRows + (adminState.locallyAddedRows ? adminState.locallyAddedRows.length : 0) - (data.deleted_count || 0));
                    // The save total covers all rows, while a search may have only one page.
                    var lastPage = currentSearch ? 1 : Math.max(1, Math.ceil(totalAfter / currentPerPage));
                    loadPage(lastPage, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
                } catch (e) {
                    loadPage(1, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
                }
            }, 500);
        } catch (error) {
            showStatus("<strong>Save failed.</strong> " + escapeHtml(error.message || "Please check the row values and try again."), "danger");
        } finally {
            saveButton.disabled = false;
            saveButton.textContent = "Save All";
        }
    }

    async function loadPage(page, search, perPage, sortBy, sortOrder) {
        if (!listUrl) {
            showStatus("List endpoint is missing.", "danger");
            return;
        }

        try {
            var params = {
                page: page,
                per_page: perPage,
                search: search,
                sort_by: sortBy,
                sort_order: sortOrder
            };

            showLoadingSpinner(true);

            // clear any inline validation rows before loading new data
            try {
                document.querySelectorAll('#sheet-body .row-error').forEach(function (el) { el.remove(); });
                document.querySelectorAll('.row-error-row').forEach(function (el) { el.remove(); });
                document.querySelectorAll('#sheet-body tr.table-danger').forEach(function (tr) { tr.classList.remove('table-danger'); });
            } catch (e) {}

            var data = await adminAPI.fetchList(listUrl, params);
            if (!data || !data.success) {
                throw new Error((data && data.error) || "Failed to load data.");
            }

            // Clear existing rows
            tableBody.innerHTML = "";

            // Add fetched rows
            data.rows.forEach(function (row) {
                if (!adminState.deletedIds.has(row.id)) addRow(adminState.pendingRows.get(row.id) || row, false);
            });

            // Include locally staged rows in totals (but only render them when showing the last page)
            var stagedCount = (adminState && adminState.locallyAddedRows) ? adminState.locallyAddedRows.length : 0;

            // Update pagination info: totalRows includes staged rows
            totalRows = (typeof data.total === "number" ? data.total : 0) + stagedCount;
            totalPages = Math.max(1, Math.ceil(totalRows / perPage));
            currentPage = page;
            currentPerPage = perPage;
            currentSearch = search;
            currentSortBy = sortBy;
            currentSortOrder = sortOrder;

            // If this is the last page (after accounting for staged rows), append staged rows to the DOM.
            // Render staged rows without the local highlight (pass isLocal = false).
            if (stagedCount > 0 && page === totalPages) {
                try {
                    adminState.locallyAddedRows.forEach(function (row) {
                        // show staged rows on last page but without 'table-info' highlight
                        addRow(row, false);
                    });
                } catch (e) {
                    // ignore DOM append errors
                }
            }

            updatePaginationUI();
            updateSortHeaders();

        } catch (error) {
            showStatus("<strong>Failed to load data.</strong> " + escapeHtml(error.message || "Please try again."), "danger");
        } finally {
            showLoadingSpinner(false);
        }
    }

    function updatePaginationUI() {
        var showingStart = (currentPage - 1) * currentPerPage + 1;
        var showingEnd = Math.min(currentPage * currentPerPage, totalRows);

        document.getElementById("showing-start").textContent = totalRows > 0 ? showingStart : 0;
        document.getElementById("showing-end").textContent = showingEnd;
        document.getElementById("total-count").textContent = totalRows;

        prevPageBtn.disabled = currentPage <= 1;
        nextPageBtn.disabled = currentPage >= totalPages;

        // Generate page numbers
        pageNumbersContainer.innerHTML = "";
        var startPage = Math.max(1, currentPage - 2);
        var endPage = Math.min(totalPages, currentPage + 2);

        if (startPage > 1) {
            var firstPageBtn = document.createElement("button");
            firstPageBtn.type = "button";
            firstPageBtn.className = "btn btn-outline-secondary btn-sm page-number";
            firstPageBtn.textContent = "1";
            firstPageBtn.addEventListener("click", function () {
                loadPage(1, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
            });
            pageNumbersContainer.appendChild(firstPageBtn);

            if (startPage > 2) {
                var ellipsis = document.createElement("span");
                ellipsis.className = "page-number";
                ellipsis.textContent = "...";
                pageNumbersContainer.appendChild(ellipsis);
            }
        }

        for (var i = startPage; i <= endPage; i++) {
            var pageBtn = document.createElement("button");
            pageBtn.type = "button";
            pageBtn.className = "btn btn-sm page-number";
            if (i === currentPage) {
                pageBtn.className += " btn-primary";
                pageBtn.disabled = true;
            } else {
                pageBtn.className += " btn-outline-secondary";
            }
            pageBtn.textContent = i;
            pageBtn.addEventListener("click", function (page) {
                return function () {
                    loadPage(page, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
                };
            }(i));
            pageNumbersContainer.appendChild(pageBtn);
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) {
                var ellipsis2 = document.createElement("span");
                ellipsis2.className = "page-number";
                ellipsis2.textContent = "...";
                pageNumbersContainer.appendChild(ellipsis2);
            }

            var lastPageBtn = document.createElement("button");
            lastPageBtn.type = "button";
            lastPageBtn.className = "btn btn-outline-secondary btn-sm page-number";
            lastPageBtn.textContent = totalPages;
            lastPageBtn.addEventListener("click", function () {
                loadPage(totalPages, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
            });
            pageNumbersContainer.appendChild(lastPageBtn);
        }
    }

    function updateSortHeaders() {
        var headers = document.querySelectorAll(".sort-header");
        headers.forEach(function (header) {
            var icon = header.querySelector(".sort-icon i");
            var column = header.dataset.sortColumn;
            if (column === currentSortBy) {
                icon.className = currentSortOrder === "asc" ? "fa fa-sort-up" : "fa fa-sort-down";
                header.querySelector(".sort-icon").classList.add("active");
            } else {
                icon.className = "fa fa-sort";
                header.querySelector(".sort-icon").classList.remove("active");
            }
        });
    }

    function showLoadingSpinner(show) {
        var spinner = document.getElementById("loading-spinner");
        if (show) {
            spinner.classList.remove("d-none");
        } else {
            spinner.classList.add("d-none");
        }
    }

    // Event Listeners
    modalSaveBtn.addEventListener("click", async function () {
        try {
            await window.shipmentDocuments.ready();
        } catch (error) {
            showStatus("<strong>Check the selected documents.</strong> " + escapeHtml(error.message || "Please select the file again."), "danger");
            return;
        }

        if (isCreatingRow) {
            var newId = adminState.nextLocalId();
            var newSource = buildRowData({}, newId);
            if (updateRowFromModal(null, newSource)) {
                // Stage row in admin state and show it immediately if possible.
                adminState.pushLocalRow(newSource);

                // Update totals and pagination UI
                totalRows = (typeof totalRows === "number" ? totalRows : 0) + 1;
                totalPages = Math.max(1, Math.ceil(totalRows / currentPerPage));
                updatePaginationUI();

                // If the new row belongs on the current page, render it immediately.
                if (currentPage === totalPages) {
                    addRow(newSource, true);
                } else {
                    // Otherwise navigate to the page that will contain the new staged row.
                    loadPage(totalPages, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
                }

                showStatus("Row staged locally. Click 'Save All' to persist changes.", "info");

                // Close modal and reset local editing state
                editModal.hide();
                currentEditingRow = null;
                isCreatingRow = false;
            }
            return;
        }

        if (currentEditingRow) {
            var source = getRowDataFromTr(currentEditingRow);
            if (updateRowFromModal(currentEditingRow, source)) {
                editModal.hide();
                currentEditingRow = null;
                isCreatingRow = false;
            }
        }
    });

    addRowButton.addEventListener("click", function () {
        currentEditingRow = null;
        isCreatingRow = true;
        clearModal();
        document.getElementById('editConsignmentLabel').textContent = 'Add Consignment';
        editModal.show();
    });

    document.getElementById("editConsignmentModal").addEventListener("shown.bs.modal", function () {
        document.getElementById("modal-consignment-number").focus();
    });

    document.getElementById("editConsignmentModal").addEventListener("hidden.bs.modal", function () {
        currentEditingRow = null;
        isCreatingRow = false;
        clearModal();
    });

    saveButton.addEventListener("click", saveSheet);

    // Search with debouncing
    searchInput.addEventListener("input", function () {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(function () {
            loadPage(1, searchInput.value.trim(), currentPerPage, currentSortBy, currentSortOrder);
        }, 500);
    });

    // Per-page selector
    perPageSelect.addEventListener("change", function () {
        currentPerPage = parseInt(perPageSelect.value);
        loadPage(1, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
    });

    // Clear filters
    clearFiltersBtn.addEventListener("click", function () {
        searchInput.value = "";
        perPageSelect.value = "10";
        currentPerPage = 10;
        currentSearch = "";
        loadPage(1, "", 10, "id", "asc");
    });

    // Pagination buttons
    prevPageBtn.addEventListener("click", function () {
        if (currentPage > 1) {
            loadPage(currentPage - 1, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
        }
    });

    nextPageBtn.addEventListener("click", function () {
        if (currentPage < totalPages) {
            loadPage(currentPage + 1, currentSearch, currentPerPage, currentSortBy, currentSortOrder);
        }
    });

    // Sort headers
    var sortHeaders = document.querySelectorAll(".sort-header");
    sortHeaders.forEach(function (header) {
        header.addEventListener("click", function () {
            var column = header.dataset.sortColumn;
            var newOrder = "asc";
            if (currentSortBy === column && currentSortOrder === "asc") {
                newOrder = "desc";
            }
            loadPage(1, currentSearch, currentPerPage, column, newOrder);
        });
    });

    // Initial load: prefer server-rendered `data-existing-rows` when present
    (function initialLoad() {
        var existingJson = tableBody.dataset.existingRows || "";
        if (existingJson) {
            try {
                var existingRows = JSON.parse(existingJson || "[]") || [];
                if (existingRows.length) {
                    tableBody.innerHTML = "";
                    // Respect rows-per-page on initial render: only render the first page
                    var displayRows = existingRows.slice(0, currentPerPage);
                    displayRows.forEach(function (row) { addRow(row, false); });
                    totalRows = existingRows.length;
                    totalPages = Math.max(1, Math.ceil(totalRows / currentPerPage));
                    currentPage = 1;
                    // Ensure the per-page select reflects the active value
                    try { if (perPageSelect) perPageSelect.value = String(currentPerPage); } catch (e) {}
                    updatePaginationUI();
                    updateSortHeaders();
                    return;
                }
            } catch (e) {
                // Fall through to API load on parse error
            }
        }

        // Fallback to paginated API load
        loadPage(1, "", currentPerPage, currentSortBy, currentSortOrder);
    })();

    // Auto-hide any server-rendered alerts on page load after 10s, unless they set data-autodismiss="false"
    try {
        setTimeout(function () {
            var alerts = document.querySelectorAll('.alert');
            alerts.forEach(function (a) {
                try {
                    if (a.dataset && a.dataset.autodismiss === 'false') return;
                    a.classList.add('d-none');
                } catch (e) {}
            });
        }, 10000);
    } catch (e) {}
});

