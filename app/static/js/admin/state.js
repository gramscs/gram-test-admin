// Admin state helpers attached to window.adminState
(function () {
  function createSet() { return new Set(); }

  var state = {
    deletedIds: createSet(),
    modifiedRowIds: createSet(),
    locallyAddedRows: [],
    newRowIdCounter: 0,

    addDeleted: function (id) { if (id) this.deletedIds.add(Number(id)); },
    clearDeleted: function () { this.deletedIds.clear(); },

    addModified: function (id) { if (id) this.modifiedRowIds.add(Number(id)); },
    clearModified: function () { this.modifiedRowIds.clear(); },

    pushLocalRow: function (row) { this.locallyAddedRows.push(row); },
    removeLocalRowById: function (id) {
      var idx = this.locallyAddedRows.findIndex(function (r) { return r.id === id; });
      if (idx !== -1) this.locallyAddedRows.splice(idx, 1);
    },
    nextLocalId: function () { this.newRowIdCounter += 1; return -(this.newRowIdCounter); },

    resetAfterSave: function () {
      this.clearDeleted();
      this.clearModified();
      this.locallyAddedRows = [];
      this.newRowIdCounter = 0;
    }
  };

  window.adminState = state;
})();
