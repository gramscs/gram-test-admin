// Admin state helpers attached to window.adminState
(function () {
  function createSet() { return new Set(); }

  var state = {
    deletedIds: createSet(),
    modifiedRowIds: createSet(),
    locallyAddedRows: [],
    pendingRows: new Map(),
    newRowIdCounter: 0,

    addDeleted: function (id) { if (id) { this.deletedIds.add(String(id)); this.pendingRows.delete(String(id)); } },
    clearDeleted: function () { this.deletedIds.clear(); },

    addModified: function (id) { if (id) this.modifiedRowIds.add(String(id)); },
    clearModified: function () { this.modifiedRowIds.clear(); },

    pushLocalRow: function (row) { this.locallyAddedRows.push(row); },
    rememberRow: function (row) {
      this.pendingRows.set(String(row.id), row);
      if (row.id && !String(row.id).startsWith('-')) this.addModified(row.id);
      var index = this.locallyAddedRows.findIndex(function (item) { return String(item.id) === String(row.id); });
      if (index !== -1) this.locallyAddedRows[index] = row;
    },
    removeLocalRowById: function (id) {
      var idx = this.locallyAddedRows.findIndex(function (r) { return String(r.id) === String(id); });
      if (idx !== -1) this.locallyAddedRows.splice(idx, 1);
      this.pendingRows.delete(String(id));
    },
    nextLocalId: function () { this.newRowIdCounter += 1; return -(this.newRowIdCounter); },

    resetAfterSave: function () {
      this.clearDeleted();
      this.clearModified();
      this.locallyAddedRows = [];
      this.pendingRows.clear();
      this.newRowIdCounter = 0;
    }
  };

  window.adminState = state;
})();
