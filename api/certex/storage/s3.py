"""S3-compatible object storage.

One adapter serves both MinIO and AWS S3. The only differences are the endpoint
URL and path-style addressing, both driven by configuration, so moving a
deployment to real S3 is an environment change rather than a code change.

Storage keys are derived from UUIDs, never from user-supplied filenames. A
filename like ``../../etc/passwd`` or ``Ahmed Khan birth.pdf`` therefore cannot
influence the key: the first is a traversal attempt, the second would place a
person's name in object storage metadata and access logs.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import uuid
from functools import lru_cache
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, cast

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from botocore.response import StreamingBody

from certex.config import S3ServerSideEncryption, Settings, get_settings
from certex.core.errors import NotFoundError, StorageUnavailableError
from certex.logging_setup import get_logger, safe_error

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef
else:  # pragma: no cover
    S3Client = object

__all__ = [
    "ObjectStorage",
    "StorageKeys",
    "get_object_storage",
]

logger = get_logger(__name__)

_SIGNATURE_VERSION: Final = "s3v4"


class StorageKeys:
    """Key layout. Every segment is a UUID, a date or a fixed literal.

    ``documents/<workspace>/<yyyy>/<mm>/<document-id>``
    ``uploads/<workspace>/<upload-session-id>``            (in-flight resumable upload)
    ``pages/<workspace>/<document-id>/<page-number>.jpg``  (review-pane page image)
    ``cache/ocr/<workspace>/<sha256>.json``                (OCR result by page hash)
    ``exports/<workspace>/<export-id>.<ext>``
    """

    @staticmethod
    def document(workspace_id: uuid.UUID, document_id: uuid.UUID, *, year: int, month: int) -> str:
        return f"documents/{workspace_id}/{year:04d}/{month:02d}/{document_id}"

    @staticmethod
    def upload(workspace_id: uuid.UUID, upload_id: uuid.UUID) -> str:
        return f"uploads/{workspace_id}/{upload_id}"

    @staticmethod
    def page_image(workspace_id: uuid.UUID, document_id: uuid.UUID, page_number: int) -> str:
        return f"pages/{workspace_id}/{document_id}/{page_number:05d}.jpg"

    @staticmethod
    def ocr_cache(workspace_id: uuid.UUID, digest: str) -> str:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("OCR cache keys are lowercase SHA-256 hex digests")
        return f"cache/ocr/{workspace_id}/{digest}.json"

    @staticmethod
    def export(workspace_id: uuid.UUID, export_id: uuid.UUID, extension: str) -> str:
        safe_extension = extension.lower().lstrip(".")
        if not safe_extension.isalnum():
            raise ValueError(f"Refusing to build a key from extension {extension!r}")
        return f"exports/{workspace_id}/{export_id}.{safe_extension}"

    @staticmethod
    def derived(workspace_id: uuid.UUID, document_id: uuid.UUID, kind: str) -> str:
        """Key for a file derived from a document, e.g. a .doc converted to .docx."""
        if not kind.replace("-", "").isalnum():
            raise ValueError(f"Refusing to build a key from kind {kind!r}")
        return f"derived/{workspace_id}/{document_id}/{kind}"


class ObjectStorage:
    """Thin, typed wrapper over the S3 API surface this system uses."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: S3Client | None = None

    # ------------------------------------------------------------------ client
    @property
    def bucket(self) -> str:
        return self._settings.s3_bucket

    @property
    def client(self) -> S3Client:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> S3Client:
        settings = self._settings
        config = BotoConfig(
            signature_version=_SIGNATURE_VERSION,
            s3={"addressing_style": "path" if settings.s3_use_path_style else "auto"},
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=10,
            read_timeout=120,
            # botocore 1.36 began sending x-amz-checksum-* on every request.
            # Several S3-compatible backends - MinIO among them - reject
            # DeleteObjects without the legacy Content-MD5 header instead.
            # "when_required" restores that behaviour for the operations that
            # need it while leaving AWS S3 unaffected.
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            region_name=settings.s3_region,
            config=config,
        )
        client.meta.events.register("before-send.s3.DeleteObjects", _add_content_md5)
        return client

    # ------------------------------------------------------------- encryption
    def encryption_args(self) -> dict[str, str]:
        """Server-side encryption parameters for every write.

        Returned as kwargs rather than applied implicitly so that each call site
        is visibly encrypting, and so a future unencrypted path would have to be
        written deliberately rather than by omission.
        """
        sse = self._settings.s3_sse
        if sse is S3ServerSideEncryption.NONE:
            return {}
        if sse is S3ServerSideEncryption.AWS_KMS:
            key_id = self._settings.s3_sse_kms_key_id
            if not key_id:  # pragma: no cover - config validator prevents this
                raise StorageUnavailableError("S3_SSE=aws:kms requires S3_SSE_KMS_KEY_ID.")
            return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": key_id}
        return {"ServerSideEncryption": "AES256"}

    # ----------------------------------------------------------- provisioning
    def bucket_exists(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if status in (403, 404):
                return False
            raise
        return True

    def ensure_bucket(self) -> bool:
        """Create the bucket when absent. Returns True when it was created.

        Also enables versioning: a mis-scoped delete or an overwrite during a
        re-run then remains recoverable, which matters when the objects are
        someone's only digitised copy of a birth record.
        """
        try:
            if self.bucket_exists():
                logger.info("storage.bucket_present", storage_key=self.bucket)
                self._ensure_versioning()
                return False

            params: dict[str, str | dict[str, str]] = {"Bucket": self.bucket}
            if self._settings.s3_region and self._settings.s3_region != "us-east-1":
                params["CreateBucketConfiguration"] = {
                    "LocationConstraint": self._settings.s3_region
                }
            self.client.create_bucket(**params)  # type: ignore[arg-type]
            logger.info("storage.bucket_created", storage_key=self.bucket)
            self._ensure_versioning()
            self._ensure_public_access_block()
            return True
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not provision the object storage bucket: {safe_error(exc)}"
            ) from exc

    def _ensure_versioning(self) -> None:
        try:
            self.client.put_bucket_versioning(
                Bucket=self.bucket,
                VersioningConfiguration={"Status": "Enabled"},
            )
        except ClientError as exc:
            # Not every S3-compatible backend implements versioning; losing it is
            # not a reason to refuse to start.
            logger.warning("storage.versioning_unavailable", error_type=safe_error(exc))

    def _ensure_public_access_block(self) -> None:
        """Belt and braces against a bucket of identity documents being public."""
        try:
            self.client.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        except ClientError as exc:
            logger.warning("storage.public_access_block_unavailable", error_type=safe_error(exc))

    # ------------------------------------------------------------------ probe
    def ping(self) -> None:
        """Raise :class:`StorageUnavailableError` when storage is not usable."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Object storage is not reachable: {safe_error(exc)}"
            ) from exc

    def verify_encryption_supported(self) -> bool:
        """Write and delete a probe object to prove SSE is actually accepted.

        MinIO rejects SSE-S3 writes unless a KMS is configured. Discovering that
        at boot is far better than discovering it on the thousandth upload.
        """
        if self._settings.s3_sse is S3ServerSideEncryption.NONE:
            return True
        key = f"_healthcheck/{uuid.uuid4()}"
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=b"certex-encryption-probe",
                **self.encryption_args(),  # type: ignore[arg-type]
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "storage.encryption_unsupported",
                error_type=safe_error(exc),
                provider=self._settings.s3_sse.value,
            )
            return False
        finally:
            # A probe object left behind is untidy but harmless; a failed cleanup
            # must not turn a successful encryption check into an error.
            with contextlib.suppress(BotoCoreError, ClientError):
                self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    # ------------------------------------------------------------- transfers
    def _transfer_config(self) -> TransferConfig:
        """Multipart thresholds.

        boto3 switches to multipart above the threshold and streams one part at a
        time, so a 500 MB scan never exists in memory as a whole.
        """
        chunk = self._settings.upload_chunk_bytes
        return TransferConfig(
            multipart_threshold=chunk,
            multipart_chunksize=chunk,
            max_concurrency=4,
            use_threads=True,
        )

    def upload_stream(
        self,
        key: str,
        fileobj: IO[bytes],
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Stream a file object to storage under ``key``.

        Rewinds before reading, so an earlier hashing pass over the same handle
        cannot leave the position at EOF and upload an empty object.
        """
        extra: dict[str, object] = {"ContentType": content_type}
        extra.update(self.encryption_args())
        if metadata:
            extra["Metadata"] = metadata

        fileobj.seek(0)
        try:
            self.client.upload_fileobj(
                fileobj,
                self.bucket,
                key,
                ExtraArgs=extra,
                Config=self._transfer_config(),
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not write the document to storage: {safe_error(exc)}"
            ) from exc

    def upload_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Write a small in-memory payload. Documents go through upload_stream."""
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
                # boto3-stubs types put_object against a TypedDict, which cannot
                # accept a **dict splat. The keys come from encryption_args and
                # are exactly the SSE parameters the API defines.
                **self.encryption_args(),  # type: ignore[arg-type]
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(f"Could not write to storage: {safe_error(exc)}") from exc

    def get_bytes_if_exists(self, key: str) -> bytes | None:
        """Read a small object whole, or None when it does not exist.

        For cache entries and page images only - both bounded in size. Documents
        are always streamed.
        """
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise StorageUnavailableError(
                f"Could not read from storage: {safe_error(exc)}"
            ) from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError(
                f"Could not read from storage: {safe_error(exc)}"
            ) from exc
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    # ------------------------------------------------------ multipart uploads
    def create_multipart_upload(
        self,
        key: str,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Begin a server-side multipart upload and return its upload id.

        Encryption is declared here, at creation: S3 applies it to every part and
        to the assembled object, so parts never need to repeat it.
        """
        try:
            response = self.client.create_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                ContentType=content_type,
                Metadata=metadata or {},
                **self.encryption_args(),  # type: ignore[arg-type]
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not start the upload in storage: {safe_error(exc)}"
            ) from exc
        return str(response["UploadId"])

    def upload_part(self, key: str, *, upload_id: str, part_number: int, payload: bytes) -> str:
        """Send one part. Returns the ETag needed to complete the upload."""
        try:
            response = self.client.upload_part(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=payload,
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not store part {part_number} of the upload: {safe_error(exc)}"
            ) from exc
        return str(response["ETag"])

    def complete_multipart_upload(
        self, key: str, *, upload_id: str, parts: list[tuple[int, str]]
    ) -> None:
        """Assemble the uploaded parts, in part-number order, into one object."""
        ordered = sorted(parts, key=lambda part: part[0])
        try:
            self.client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [{"PartNumber": number, "ETag": etag} for number, etag in ordered]
                },
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not assemble the uploaded file: {safe_error(exc)}"
            ) from exc

    def abort_multipart_upload(self, key: str, *, upload_id: str) -> None:
        """Discard an unfinished upload and every part already sent.

        A missing upload is not an error: aborting is the cleanup path, and it may
        run twice or after the storage side has already expired the upload.
        """
        try:
            self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchUpload", "404"):
                return
            logger.warning("storage.abort_failed", storage_key=key, error_type=safe_error(exc))
        except BotoCoreError as exc:
            logger.warning("storage.abort_failed", storage_key=key, error_type=safe_error(exc))

    def copy_object(
        self,
        source_key: str,
        destination_key: str,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        """Server-side copy, re-encrypted and with replaced metadata.

        Moving a finished upload to its document key this way costs no bandwidth:
        the bytes never leave the storage service.
        """
        try:
            self.client.copy_object(
                Bucket=self.bucket,
                Key=destination_key,
                CopySource={"Bucket": self.bucket, "Key": source_key},
                ContentType=content_type,
                Metadata=metadata or {},
                MetadataDirective="REPLACE",
                **self.encryption_args(),  # type: ignore[arg-type]
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not move the uploaded file into place: {safe_error(exc)}"
            ) from exc

    def download_stream(self, key: str) -> StreamingBody:
        """Open an object for reading. The caller must close the returned body."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise NotFoundError(
                    "The stored document is no longer available.",
                    remediation=(
                        "It may have been removed by the retention policy. "
                        "Re-upload the file to process it again."
                    ),
                ) from exc
            raise StorageUnavailableError(
                f"Could not read the document from storage: {safe_error(exc)}"
            ) from exc
        return response["Body"]

    def download_to_path(self, key: str, destination: Path) -> Path:
        """Download an object to a local path, streaming in constant memory.

        A missing object raises :class:`NotFoundError` - permanent, never worth a
        retry - while every other failure raises :class:`StorageUnavailableError`.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("wb") as handle:
                self.client.download_fileobj(
                    self.bucket, key, handle, Config=self._transfer_config()
                )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise NotFoundError(
                    "The stored document is no longer available.",
                    remediation=(
                        "It may have been removed by the retention policy. "
                        "Re-upload the file to process it again."
                    ),
                ) from exc
            raise StorageUnavailableError(
                f"Could not read the document from storage: {safe_error(exc)}"
            ) from exc
        except BotoCoreError as exc:
            raise StorageUnavailableError(
                f"Could not read the document from storage: {safe_error(exc)}"
            ) from exc
        return destination

    def object_exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        """Delete one object. A missing object is not an error."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.warning("storage.delete_failed", storage_key=key, error_type=safe_error(exc))

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a prefix. Returns how many were removed.

        Used by batch deletion and the retention sweep, where an orphaned blob
        would mean personal data outliving its retention period.
        """
        if not prefix or prefix in ("/", "*"):
            raise ValueError("Refusing to delete an unbounded prefix")

        removed = 0
        paginator = self.client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if not keys:
                    continue
                # delete_objects caps at 1000 keys per call, which matches the
                # paginator's default page size.
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": cast("list[ObjectIdentifierTypeDef]", keys)},
                )
                removed += len(keys)
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not purge stored documents: {safe_error(exc)}"
            ) from exc

        logger.info("storage.prefix_deleted", storage_key=prefix, count=removed)
        return removed

    def presign_get(self, key: str, *, filename: str | None = None) -> str:
        """Time-limited read URL, used by the review pane for page images."""
        params: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
        try:
            return str(
                self.client.generate_presigned_url(
                    "get_object",
                    Params=params,
                    ExpiresIn=self._settings.s3_presign_ttl_seconds,
                )
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailableError(
                f"Could not create a document link: {safe_error(exc)}"
            ) from exc


def _add_content_md5(request: object, **_kwargs: object) -> None:
    """Attach the legacy ``Content-MD5`` header to a DeleteObjects request.

    The S3 API requires an integrity header on bulk delete. botocore 1.36 switched
    from ``Content-MD5`` to ``x-amz-checksum-crc32``, which S3-compatible backends
    released before that change reject outright. Setting the legacy header keeps
    bulk delete - and therefore batch purge and the retention sweep - working
    against MinIO and older gateways as well as AWS.

    The digest is a transport integrity check mandated by the protocol, not a
    security control, which is why MD5 is both correct and sufficient here.
    """
    body = getattr(request, "body", None)
    headers = getattr(request, "headers", None)
    if not isinstance(body, bytes) or headers is None:
        return
    if "Content-MD5" in headers:
        return
    digest = hashlib.md5(body, usedforsecurity=False).digest()
    headers["Content-MD5"] = base64.b64encode(digest).decode("ascii")


@lru_cache(maxsize=1)
def get_object_storage() -> ObjectStorage:
    return ObjectStorage()
