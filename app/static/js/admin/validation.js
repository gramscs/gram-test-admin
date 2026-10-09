// Lightweight validation utilities attached to window.adminValidation
(function () {
  function validatePincode(value) {
    var raw = (value || '').trim();
    if (raw === '') return true;
    return /^[1-9][0-9]{5}$/.test(raw);
  }

  function normalizePincode(value) {
    var raw = (value || '').trim();
    if (raw === '') return '';
    return raw;
  }

  function validateRow(row) {
    var errors = [];
    var cn = (row && row.consignment_number) ? String(row.consignment_number).trim() : "";
    var status = (row && row.status) ? String(row.status).trim() : "";
    var pickup = (row && row.pickup_pincode) ? String(row.pickup_pincode).trim() : "";
    var drop = (row && row.drop_pincode) ? String(row.drop_pincode).trim() : "";

    if (!cn) {
      errors.push({ field: 'consignment_number', message: 'Consignment number is required.' });
    }
    if (!status) {
      errors.push({ field: 'status', message: 'Status is required.' });
    }
    if (pickup && !validatePincode(pickup)) {
      errors.push({ field: 'pickup_pincode', message: 'Invalid pickup pincode.' });
    }
    if (drop && !validatePincode(drop)) {
      errors.push({ field: 'drop_pincode', message: 'Invalid drop pincode.' });
    }

    return { valid: errors.length === 0, errors: errors };
  }

  window.adminValidation = {
    validatePincode: validatePincode,
    normalizePincode: normalizePincode
  };
  // backward-compatible export
  window.adminValidation.validateRow = validateRow;
})();
