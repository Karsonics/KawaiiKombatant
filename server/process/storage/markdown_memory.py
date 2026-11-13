"""Human-editable Markdown storage for personal memories."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import yaml


MEMORY_TYPES = frozenset({"fact", "preference", "experience", "temporary"})
EPISTEMIC_STATUSES = frozenset({"confirmed", "inferred"})
MEMORY_STATUSES = frozenset({"active", "archived", "superseded"})
_TYPE_DIRECTORIES = {
    "fact": "facts",
    "preference": "preferences",
    "experience": "experiences",
    "temporary": "temporary",
}
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MEMORY_ID_RE = re.compile(r"^mem-(?:[0-9]{6,}|[0-9a-f]{32})$")
_METADATA_KEYS = {
    "id",
    "user_id",
    "type",
    "epistemic_status",
    "status",
    "created",
    "updated",
    "source_session",
    "last_confirmed",
    "expires",
    "tags",
}
_LOCKS_GUARD = threading.Lock()
_VAULT_LOCKS: dict[str, threading.RLock] = {}
_UNSET = object()


class _UniqueKeyLoader(yaml.SafeLoader):
    yaml_implicit_resolvers = {
        key: [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag != "tag:yaml.org,2002:timestamp"
        ]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


def _construct_unique_mapping(loader: _UniqueKeyLoader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise InvalidMemoryError("Memory frontmatter contains duplicate keys")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class MemoryStoreError(Exception):
    """Base exception for memory storage errors."""


class InvalidMemoryError(MemoryStoreError):
    """A memory object or Markdown file does not match the storage contract."""


class MemoryConflictError(MemoryStoreError):
    """The file changed after it was read and must not be overwritten."""


class MemoryNotFoundError(MemoryStoreError):
    """The requested memory does not exist for the specified user."""


class VaultError(MemoryStoreError):
    """The explicitly configured vault cannot be accessed or written."""


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    user_id: str
    type: str
    epistemic_status: str
    status: str
    created: str
    updated: str
    content: str
    source_session: str | None = None
    last_confirmed: str | None = None
    expires: str | None = None
    tags: tuple[str, ...] = ()
    # Hash of the exact file content from which this record was read.
    version: str | None = None


class MarkdownMemoryStore:
    """Store one validated memory per Markdown file in an explicit vault.

    Writes are serialized within this process. Version checks detect edits made
    since a record was read. Search excludes records at or past their UTC expiry
    instant without changing the files. This is not a multi-process locking
    guarantee.
    """

    def __init__(
        self,
        vault_path: str | os.PathLike[str],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if vault_path is None or not str(vault_path).strip():
            raise VaultError("An explicit, non-empty vault path is required")
        self.vault_path = Path(vault_path).expanduser().resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        lock_key = os.path.normcase(str(self.vault_path))
        with _LOCKS_GUARD:
            self._lock = _VAULT_LOCKS.setdefault(lock_key, threading.RLock())
        self.diagnostics: list[str] = []

    def initialize_vault(self) -> None:
        """Create the explicitly selected vault root without replacing files."""
        try:
            if self.vault_path.exists() and not self.vault_path.is_dir():
                raise VaultError("Vault path exists and is not a directory")
            self.vault_path.mkdir(parents=True, exist_ok=True)
            (self.vault_path / "users").mkdir(exist_ok=True)
            readme = self.vault_path / "README.md"
            try:
                with readme.open("x", encoding="utf-8") as handle:
                    handle.write(
                        "# Kuro Memory Vault\n\n"
                        "Personal memory files managed by Kuro. "
                        "Obsidian is optional; these files are plain Markdown.\n"
                    )
            except FileExistsError:
                pass
        except OSError as exc:
            raise VaultError("Unable to initialize the configured vault") from exc

    def initialize_user(self, user_id: str) -> Path:
        """Create the directory structure for one validated user."""
        user_dir = self._user_dir(user_id)
        try:
            if not self.vault_path.is_dir():
                raise VaultError("Vault is not initialized")
            user_dir.mkdir(parents=True, exist_ok=True)
            for directory in ("facts", "preferences", "experiences", "temporary"):
                (user_dir / directory).mkdir(exist_ok=True)
            profile = user_dir / "profile.md"
            try:
                with profile.open("x", encoding="utf-8") as handle:
                    handle.write(f"# Profile: {user_id}\n\n")
            except FileExistsError:
                pass
            return user_dir
        except OSError as exc:
            raise VaultError("Unable to initialize the user memory directory") from exc

    def create_memory(
        self,
        user_id: str,
        memory_type: str,
        content: str,
        *,
        epistemic_status: str = "confirmed",
        status: str = "active",
        source_session: str | None = None,
        last_confirmed: str | None = None,
        expires: str | None = None,
        tags: Iterable[str] = (),
        memory_id: str | None = None,
    ) -> MemoryRecord:
        with self._lock:
            tag_values = tuple(tags)
            self._check_values(
                memory_type, epistemic_status, status, content, tag_values
            )
            user_dir = self._require_user_dir(user_id)
            if memory_id is None:
                memory_id = f"mem-{uuid.uuid4().hex}"
            self._validate_id(memory_id)
            if self._find_id(user_dir, memory_id) is not None:
                raise MemoryConflictError(f"Memory ID already exists: {memory_id}")
            now = _utc_now()
            record = MemoryRecord(
                id=memory_id,
                user_id=user_id,
                type=memory_type,
                epistemic_status=epistemic_status,
                status=status,
                created=now,
                updated=now,
                content=content,
                source_session=source_session,
                last_confirmed=last_confirmed,
                expires=expires,
                tags=tag_values,
            )
            _validate_optional_metadata(record)
            path = self._memory_path(user_dir, record)
            self._atomic_write(path, _serialize(record))
            return self._read_path(path, user_id)

    def get_memory(self, user_id: str, memory_id: str) -> MemoryRecord:
        self._validate_id(memory_id)
        if not self.vault_path.is_dir():
            raise VaultError("Vault is missing or unavailable")
        path = self._find_id(self._user_dir(user_id), memory_id)
        if path is None:
            raise MemoryNotFoundError(f"Memory not found: {memory_id}")
        return self._read_path(path, user_id)

    def list_memories(
        self,
        user_id: str,
        *,
        memory_type: str | None = None,
        epistemic_status: str | None = None,
        status: str | None = None,
        active: bool | None = None,
        tags: Iterable[str] | None = None,
    ) -> list[MemoryRecord]:
        user_dir = self._user_dir(user_id)
        if not user_dir.is_dir():
            raise VaultError("User memory directory is missing")
        if memory_type is not None and memory_type not in MEMORY_TYPES:
            raise InvalidMemoryError("Unsupported memory type filter")
        if epistemic_status is not None and epistemic_status not in EPISTEMIC_STATUSES:
            raise InvalidMemoryError("Unsupported epistemic status filter")
        if status is not None and status not in MEMORY_STATUSES:
            raise InvalidMemoryError("Unsupported memory status filter")
        requested_tags = set(tags or ())
        self.diagnostics = []
        parsed_records: list[tuple[Path, MemoryRecord]] = []
        duplicate_files: dict[str, list[str]] = {}
        seen_files: dict[str, str] = {}
        try:
            paths = sorted(user_dir.rglob("mem-*.md"))
        except OSError as exc:
            raise VaultError("Unable to list memories for this user") from exc
        for path in paths:
            try:
                if not path.is_file() or not _is_within(path, user_dir):
                    continue
                record = self._read_path(
                    path, user_id, validate_directory=False
                )
            except (OSError, InvalidMemoryError, UnicodeError, yaml.YAMLError):
                self.diagnostics.append(f"Skipped invalid memory file: {path.name}")
                continue
            file_name = path.relative_to(user_dir).as_posix()
            if record.id in seen_files:
                duplicate_files.setdefault(
                    record.id, [seen_files[record.id]]
                ).append(file_name)
            else:
                seen_files[record.id] = file_name
            parsed_records.append((path, record))
        records: list[MemoryRecord] = []
        for path, record in parsed_records:
            if record.id in duplicate_files:
                continue
            if path.parent.name != _TYPE_DIRECTORIES[record.type]:
                self.diagnostics.append(f"Skipped invalid memory file: {path.name}")
                continue
            if memory_type and record.type != memory_type:
                continue
            if epistemic_status and record.epistemic_status != epistemic_status:
                continue
            if status and record.status != status:
                continue
            if active is not None and (record.status == "active") != active:
                continue
            if requested_tags and not requested_tags.issubset(record.tags):
                continue
            records.append(record)
        if duplicate_files:
            self.diagnostics.extend(
                "Skipped duplicate memory ID "
                f"{memory_id} in files: {', '.join(sorted(set(file_names)))}"
                for memory_id, file_names in sorted(duplicate_files.items())
            )
        return records

    def search_memories(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 5,
        max_chars: int = 4000,
    ) -> list[MemoryRecord]:
        """Return active fact, preference, and experience records relevant to query.

        Matching is deliberately local and deterministic: query terms are scored
        against note content, tags, and type. Temporary notes are excluded from
        persistent cross-session recall.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars < 1
        ):
            raise ValueError("max_chars must be a positive integer")

        terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
        if not terms:
            return []
        records = self.list_memories(user_id, active=True)
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        now = now.astimezone(timezone.utc)
        scored: list[tuple[int, str, MemoryRecord]] = []
        for record in records:
            if record.type not in {"fact", "preference", "experience"}:
                continue
            if _is_expired(record, now):
                continue
            text = f"{record.type} {' '.join(record.tags)} {record.content}".casefold()
            words = re.findall(r"[a-z0-9]+", text)
            overlap = terms.intersection(words)
            if overlap:
                score = sum(words.count(term) for term in overlap)
                scored.append((score, record.updated, record))
        scored.sort(key=lambda item: (-item[0], item[1], item[2].id))

        selected: list[MemoryRecord] = []
        used_chars = 0
        for _, _, record in scored:
            rendered_size = len(record.content) + 80
            if len(selected) >= limit or used_chars + rendered_size > max_chars:
                continue
            selected.append(record)
            used_chars += rendered_size
        return selected

    def update_memory(
        self,
        record: MemoryRecord,
        *,
        content: str | None = None,
        memory_type: str | None = None,
        epistemic_status: str | None = None,
        status: str | None = None,
        source_session: str | None | object = _UNSET,
        last_confirmed: str | None | object = _UNSET,
        expires: str | None | object = _UNSET,
        tags: Iterable[str] | None = None,
        confirmed: bool = False,
    ) -> MemoryRecord:
        """Update a previously read record, rejecting stale file versions."""
        if status is not None and status != record.status and confirmed is not True:
            raise PermissionError("Memory lifecycle changes require confirmation")
        self._validate_id(record.id)
        self._user_dir(record.user_id)
        if not record.version:
            raise MemoryConflictError(
                "Update requires a versioned record from this store"
            )
        with self._lock:
            user_dir = self._user_dir(record.user_id)
            path = self._find_id(user_dir, record.id)
            if path is None:
                raise MemoryNotFoundError(f"Memory not found: {record.id}")
            current_bytes = path.read_bytes()
            if _sha256(current_bytes) != record.version:
                raise MemoryConflictError(
                    f"Memory changed since it was read: {record.id}"
                )
            current = _parse_document(current_bytes, record.user_id)
            updated = replace(
                current,
                type=memory_type if memory_type is not None else current.type,
                epistemic_status=(
                    epistemic_status
                    if epistemic_status is not None
                    else current.epistemic_status
                ),
                status=status if status is not None else current.status,
                content=content if content is not None else current.content,
                source_session=(
                    current.source_session
                    if source_session is _UNSET
                    else source_session
                ),
                last_confirmed=(
                    current.last_confirmed
                    if last_confirmed is _UNSET
                    else last_confirmed
                ),
                expires=current.expires if expires is _UNSET else expires,
                tags=tuple(tags) if tags is not None else current.tags,
                updated=_utc_now(),
                version=None,
            )
            self._check_values(
                updated.type,
                updated.epistemic_status,
                updated.status,
                updated.content,
                updated.tags,
            )
            _validate_optional_metadata(updated)
            # Recheck before replacement to catch edits during serialization.
            if _sha256(path.read_bytes()) != record.version:
                raise MemoryConflictError(f"Memory changed during update: {record.id}")
            destination = self._memory_path(user_dir, updated)
            if destination != path:
                if destination.exists():
                    raise MemoryConflictError(
                        f"A file already exists for memory: {record.id}"
                    )
                self._atomic_write(destination, _serialize(updated))
                try:
                    if _sha256(path.read_bytes()) != record.version:
                        destination.unlink(missing_ok=True)
                        raise MemoryConflictError(
                            f"Memory changed during update: {record.id}"
                        )
                    path.unlink()
                except OSError as exc:
                    try:
                        destination.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise VaultError(
                        "Unable to move memory; the original file was preserved"
                    ) from exc
                return self._read_path(destination, record.user_id)
            self._atomic_write(path, _serialize(updated))
            return self._read_path(path, record.user_id)

    def delete_memory(
        self,
        user_id: str,
        memory_id: str,
        *,
        expected_version: str,
        confirmed: bool = False,
    ) -> None:
        if confirmed is not True:
            raise PermissionError("Memory deletion requires confirmation")
        self._validate_id(memory_id)
        if not self.vault_path.is_dir():
            raise VaultError("Vault is missing or unavailable")
        with self._lock:
            user_dir = self._user_dir(user_id)
            path = self._find_id(user_dir, memory_id)
            if path is None:
                raise MemoryNotFoundError(f"Memory not found: {memory_id}")
            try:
                if _sha256(path.read_bytes()) != expected_version:
                    raise MemoryConflictError(
                        f"Memory changed since it was read: {memory_id}"
                    )
                path.unlink()
            except OSError as exc:
                raise VaultError("Unable to delete memory file") from exc

    def _check_values(
        self,
        memory_type: str,
        epistemic_status: str,
        status: str,
        content: str,
        tags: Iterable[str],
    ) -> None:
        if memory_type not in MEMORY_TYPES:
            raise InvalidMemoryError("Unsupported memory type")
        if epistemic_status not in EPISTEMIC_STATUSES:
            raise InvalidMemoryError("Unsupported epistemic status")
        if status not in MEMORY_STATUSES:
            raise InvalidMemoryError("Unsupported memory status")
        if not isinstance(content, str) or not content.strip():
            raise InvalidMemoryError("Memory content must be non-empty text")
        if any(not isinstance(tag, str) or not tag.strip() for tag in tags):
            raise InvalidMemoryError("Tags must be non-empty strings")

    def _user_dir(self, user_id: str) -> Path:
        if not isinstance(user_id, str) or not _USER_ID_RE.fullmatch(user_id):
            raise InvalidMemoryError("Invalid user ID")
        path = self.vault_path / "users" / user_id
        if not _is_within(path, self.vault_path):
            raise InvalidMemoryError("User path escapes the vault")
        return path

    @staticmethod
    def _validate_id(memory_id: str) -> None:
        if not isinstance(memory_id, str) or not _MEMORY_ID_RE.fullmatch(memory_id):
            raise InvalidMemoryError("Invalid memory ID")

    def _require_user_dir(self, user_id: str) -> Path:
        if not self.vault_path.is_dir():
            raise VaultError("Vault is missing or unavailable; initialize it first")
        user_dir = self._user_dir(user_id)
        if not user_dir.exists():
            return self.initialize_user(user_id)
        if not user_dir.is_dir() or user_dir.is_symlink():
            raise VaultError("User memory path is not a safe directory")
        return user_dir

    def _memory_path(self, user_dir: Path, record: MemoryRecord) -> Path:
        path = user_dir / _TYPE_DIRECTORIES[record.type] / f"{record.id}.md"
        if not _is_within(path, user_dir):
            raise InvalidMemoryError("Memory path escapes the user directory")
        return path

    def _find_id(self, user_dir: Path, memory_id: str) -> Path | None:
        if not user_dir.is_dir():
            return None
        try:
            matches = [
                p
                for p in user_dir.rglob(f"{memory_id}.md")
                if p.is_file() and _is_within(p, user_dir)
            ]
        except OSError as exc:
            raise VaultError("Unable to search this user's memory files") from exc
        if len(matches) > 1:
            raise MemoryConflictError(f"Duplicate memory ID found: {memory_id}")
        return matches[0] if matches else None

    def _read_path(
        self, path: Path, user_id: str, *, validate_directory: bool = True
    ) -> MemoryRecord:
        try:
            data = path.read_bytes()
            record = _parse_document(data, user_id)
            if path.stem != record.id:
                raise InvalidMemoryError("Filename does not match memory ID")
            if (
                validate_directory
                and path.parent.name != _TYPE_DIRECTORIES[record.type]
            ):
                raise InvalidMemoryError("Memory type does not match its directory")
            return replace(record, version=_sha256(data))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise InvalidMemoryError("Unable to read or parse memory file") from exc

    def _atomic_write(self, path: Path, text: str) -> None:
        temporary: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
        except OSError as exc:
            raise VaultError(
                "Unable to write memory file; existing data was preserved"
            ) from exc
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def _serialize(record: MemoryRecord) -> str:
    metadata = {
        "id": record.id,
        "user_id": record.user_id,
        "type": record.type,
        "epistemic_status": record.epistemic_status,
        "status": record.status,
        "created": record.created,
        "updated": record.updated,
        "source_session": record.source_session,
        "last_confirmed": record.last_confirmed,
        "expires": record.expires,
        "tags": list(record.tags),
    }
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{frontmatter}\n---\n\n{record.content.rstrip()}\n"


def _parse_document(data: bytes, expected_user_id: str) -> MemoryRecord:
    text = data.decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise InvalidMemoryError("Missing or malformed YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise InvalidMemoryError("Missing closing frontmatter delimiter") from exc
    try:
        metadata = yaml.load("\n".join(lines[1:closing]), Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise InvalidMemoryError("Memory frontmatter is malformed") from exc
    if not isinstance(metadata, dict) or set(metadata) != _METADATA_KEYS:
        raise InvalidMemoryError("Memory metadata has missing or unknown fields")
    content = "\n".join(lines[closing + 1 :]).strip()
    required_strings = (
        "id",
        "user_id",
        "type",
        "epistemic_status",
        "status",
        "created",
        "updated",
    )
    if any(not isinstance(metadata.get(key), str) for key in required_strings):
        raise InvalidMemoryError("Memory metadata contains invalid required values")
    if metadata["user_id"] != expected_user_id:
        raise InvalidMemoryError("Memory user ID does not match its directory")
    if not _MEMORY_ID_RE.fullmatch(metadata["id"]):
        raise InvalidMemoryError("Memory ID is invalid")
    if metadata["type"] not in MEMORY_TYPES:
        raise InvalidMemoryError("Memory type is invalid")
    if metadata["epistemic_status"] not in EPISTEMIC_STATUSES:
        raise InvalidMemoryError("Epistemic status is invalid")
    if metadata["status"] not in MEMORY_STATUSES:
        raise InvalidMemoryError("Memory status is invalid")
    if not content:
        raise InvalidMemoryError("Memory content is empty")
    for key in ("created", "updated"):
        _validate_timestamp(metadata[key], nullable=False)
    for key in ("last_confirmed", "expires"):
        _validate_timestamp(metadata[key], nullable=True)
    for key in ("source_session",):
        if metadata[key] is not None and not isinstance(metadata[key], str):
            raise InvalidMemoryError(f"{key} must be text or null")
    tags = metadata["tags"]
    if not isinstance(tags, list) or any(
        not isinstance(tag, str) or not tag.strip() for tag in tags
    ):
        raise InvalidMemoryError("Tags must be a list of non-empty strings")
    return MemoryRecord(
        id=metadata["id"],
        user_id=metadata["user_id"],
        type=metadata["type"],
        epistemic_status=metadata["epistemic_status"],
        status=metadata["status"],
        created=metadata["created"],
        updated=metadata["updated"],
        content=content,
        source_session=metadata["source_session"],
        last_confirmed=metadata["last_confirmed"],
        expires=metadata["expires"],
        tags=tuple(tags),
    )


def _validate_timestamp(value: str | None, *, nullable: bool) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str):
        raise InvalidMemoryError("Timestamp must be an ISO-8601 string or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidMemoryError("Timestamp is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise InvalidMemoryError("Timestamps must use UTC")


def _is_expired(record: MemoryRecord, now: datetime) -> bool:
    """Treat expiry as an exclusive UTC cutoff; malformed values fail closed."""
    if record.expires is None:
        return False
    try:
        expires = datetime.fromisoformat(record.expires.replace("Z", "+00:00"))
        if (
            expires.tzinfo is None
            or expires.utcoffset() != timezone.utc.utcoffset(expires)
        ):
            return True
        return expires.astimezone(timezone.utc) <= now
    except (AttributeError, TypeError, ValueError):
        return True


def _validate_optional_metadata(record: MemoryRecord) -> None:
    if record.source_session is not None and not isinstance(record.source_session, str):
        raise InvalidMemoryError("source_session must be text or null")
    _validate_timestamp(record.last_confirmed, nullable=True)
    _validate_timestamp(record.expires, nullable=True)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False
