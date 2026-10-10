"""Read-only inventory of the existing private upload storage."""

from dataclasses import dataclass
import hashlib
import io
from pathlib import Path, PurePosixPath
import re


class MigrationError(Exception):
    """A safe message suitable for CLI output; never include connection strings."""


@dataclass(frozen=True)
class StoredFile:
    reference: str
    bucket: str
    path: str
    filename: str
    mime_type: str
    size_bytes: int
    checksum: str


class Storage:
    def __init__(self, uploads_dir, supabase_client=None, bucket="pod-uploads"):
        self.root = Path(uploads_dir).resolve()
        self.client = supabase_client
        self.bucket = bucket

    def location(self, reference):
        if not isinstance(reference, str) or not reference or "\x00" in reference:
            raise MigrationError("An upload has an invalid storage reference.")
        if reference.startswith("supabase:"):
            bucket, separator, path = reference[len("supabase:"):].partition("/")
            parts = path.split("/")
            if (not separator or bucket in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", bucket)
                    or any(part in ("", ".", "..") for part in parts) or "\\" in path):
                raise MigrationError("An upload has an invalid Supabase object reference.")
            return bucket, f"supabase:{bucket}/{path}", path
        if reference.lower().startswith(("http:", "https:", "data:", "file:")) or "\\" in reference:
            raise MigrationError("External/unsupported upload references must be moved to private admin storage first.")
        path = (self.root / reference).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise MigrationError("An upload reference escapes the configured uploads folder.")
        relative = path.relative_to(self.root).as_posix()
        return "local", "local:" + relative, path

    def describe(self, reference):
        bucket, canonical, location = self.location(reference)
        try:
            if bucket == "local" and not canonical.startswith("supabase:"):
                with location.open("rb") as source:
                    result = self._metadata(source, reference, bucket, canonical, location.name)
            else:
                if self.client is None:
                    raise MigrationError("Supabase uploads require SUPABASE_URL and SUPABASE_KEY in the environment.")
                content = self.client.storage.from_(bucket).download(location)
                result = self._metadata(io.BytesIO(content), reference, bucket, canonical, PurePosixPath(location).name)
            return result
        except MigrationError:
            raise
        except FileNotFoundError:
            raise MigrationError("A referenced upload is missing. Restore it before migration.") from None
        except Exception:
            raise MigrationError("An upload could not be read. Check storage access before migration.") from None

    @staticmethod
    def _metadata(source, reference, bucket, path, filename):
        checksum, size, header = hashlib.sha256(), 0, b""
        while block := source.read(1024 * 1024):
            if not header:
                header = block[:32]
            checksum.update(block)
            size += len(block)
        mime = "application/octet-stream"
        if header.startswith(b"%PDF-"):
            mime = "application/pdf"
        elif header.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif header.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            mime = "image/webp"
        elif header.startswith(b"PK\x03\x04") and filename.lower().endswith(".xlsx"):
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif filename.lower().endswith(".csv"):
            mime = "text/csv"
        return StoredFile(reference, bucket, path, filename, mime, size, checksum.hexdigest())

    def inventory(self, references):
        """Include unlinked local files and this app's Supabase namespace."""
        try:
            if self.root.exists():
                for path in sorted(self.root.rglob("*")):
                    if path.is_symlink():
                        raise MigrationError("Move symlinked uploads into regular files before migration.")
                    if path.is_file():
                        yield path.relative_to(self.root).as_posix()
            buckets = {self.location(value)[0] for value in references if value.startswith("supabase:")}
            if self.client is not None:
                buckets.add(self.bucket)
            for bucket in sorted(buckets):
                if self.client is None:
                    raise MigrationError("Supabase uploads require SUPABASE_URL and SUPABASE_KEY.")
                folders, visited = ["consignments"], set()
                while folders:
                    folder = folders.pop()
                    if folder in visited or len(folder.split("/")) > 64:
                        raise MigrationError("Supabase storage returned an invalid folder listing.")
                    visited.add(folder)
                    offset, names = 0, set()
                    while True:
                        entries = self.client.storage.from_(bucket).list(folder, {
                            "limit": 100, "offset": offset, "sortBy": {"column": "name", "order": "asc"},
                        })
                        if not isinstance(entries, list):
                            raise MigrationError("Supabase storage listing failed.")
                        for entry in entries:
                            name = entry.get("name")
                            if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or name in names:
                                raise MigrationError("Supabase storage returned an invalid object name.")
                            names.add(name)
                            path = folder + "/" + name
                            if entry.get("id") is None and entry.get("metadata") is None:
                                folders.append(path)
                            else:
                                yield f"supabase:{bucket}/{path}"
                        if len(entries) < 100:
                            break
                        offset += len(entries)
        except MigrationError:
            raise
        except Exception:
            raise MigrationError("The upload inventory could not be completed. No files will be skipped.") from None
