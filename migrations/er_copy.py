"""Copy a complete ER source without replacing its identities or history."""
from uuid import UUID, uuid5

from sqlalchemy import Date, JSON, Uuid

from database.models import Base, UTCDateTime
from migrations.shipment_v1 import timestamp
from migrations.storage import MigrationError


def build_er_plan(source_rows, storage, namespace):
    rows = {name: [] for name in Base.metadata.tables}
    warnings = []
    for table in Base.metadata.sorted_tables:
        for original in source_rows[table.name]:
            record = {}
            for column in table.c:
                value = original[column.name]
                if value is not None:
                    if isinstance(column.type, Uuid):
                        value = UUID(str(value))
                    elif isinstance(column.type, UTCDateTime):
                        value = timestamp(value, f"{table.name}.{column.name}")
                    elif isinstance(column.type, JSON) and isinstance(value, str):
                        import json
                        value = json.loads(value)
                    elif isinstance(column.type, Date) and isinstance(value, str):
                        from datetime import date
                        value = date.fromisoformat(value)
                record[column.name] = value
            rows[table.name].append(record)

    # Preserve the original file UUIDs: documents, historical versions and
    # reports already refer to them. Verify bytes instead of trusting metadata.
    references, manifest, paths = set(), {}, set()
    for record in rows["files"]:
        path, bucket = record["storage_path"], record["storage_bucket"]
        if path.startswith("local:"):
            reference = path[len("local:"):]
        elif path.startswith("supabase:"):
            reference = path
        else:
            reference = path if bucket == "local" else f"supabase:{bucket}/{path}"
        metadata = storage.describe(reference)
        if metadata.bucket != bucket:
            raise MigrationError("A file's storage bucket conflicts with its path.")
        if metadata.path in paths:
            raise MigrationError("Multiple ER file records refer to the same storage object.")
        if metadata.size_bytes != record["size_bytes"]:
            raise MigrationError("A file's recorded byte count differs from storage. Restore or reconcile it before migration.")
        if record["checksum"] is not None and metadata.checksum != record["checksum"].lower():
            raise MigrationError("A file's checksum differs from storage. Restore or reconcile it before migration.")
        if path != metadata.path:
            warnings.append("An ER file path was canonicalized; its original value remains in the archive.")
        record["storage_path"] = metadata.path
        paths.add(metadata.path)
        references.add(reference)
        manifest[metadata.path] = {"path": metadata.path, "size": metadata.size_bytes, "checksum": metadata.checksum}

    # Also retain uploads that currently have no database link.
    for reference in sorted(set(storage.inventory(references))):
        _, canonical, _ = storage.location(reference)
        if canonical in paths:
            continue
        metadata = storage.describe(reference)
        paths.add(metadata.path)
        rows["files"].append({"id": uuid5(namespace, "files:" + metadata.path),
            "uploaded_by": None, "storage_bucket": metadata.bucket, "storage_path": metadata.path,
            "original_filename": metadata.filename, "mime_type": metadata.mime_type,
            "size_bytes": metadata.size_bytes, "checksum": metadata.checksum, "uploaded_at": None})
        manifest[metadata.path] = {"path": metadata.path, "size": metadata.size_bytes, "checksum": metadata.checksum}
    return rows, [manifest[path] for path in sorted(manifest)], warnings
