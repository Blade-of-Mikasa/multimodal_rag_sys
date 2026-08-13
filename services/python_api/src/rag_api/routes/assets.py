"""Two-phase direct-to-object-storage asset upload endpoints."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from http import HTTPStatus
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Request

from rag_api.errors import ApiError
from rag_api.models import (
    CompleteAssetUploadResponse,
    InitiateAssetUploadRequest,
    InitiateAssetUploadResponse,
)
from rag_api.request_context import get_request_id
from rag_api.storage import ObjectStoreError
from rag_api.uploads.domain import (
    UnsupportedMediaTypeError,
    UploadNotFoundError,
    UploadNotReadyError,
    UploadTooLargeError,
    UploadValidationError,
)
from rag_api.uploads.service import AssetUploadService


router = APIRouter(prefix="/assets", tags=["assets"])
SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
SAFE_TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


@dataclass(frozen=True, slots=True)
class RequestPrincipal:
    tenant_id: str
    user_id: str


def request_principal(
    x_tenant_id: Annotated[
        str,
        Header(alias="X-Tenant-ID", min_length=1, max_length=64),
    ],
    x_user_id: Annotated[
        str,
        Header(
            alias="X-User-ID",
            min_length=1,
            max_length=128,
            pattern=SAFE_ID_PATTERN,
        ),
    ],
) -> RequestPrincipal:
    if not _is_safe_tenant_id(x_tenant_id):
        raise ApiError(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="X-Tenant-ID contains unsupported characters",
        )
    return RequestPrincipal(tenant_id=x_tenant_id, user_id=x_user_id)


def upload_service(request: Request) -> AssetUploadService:
    return request.app.state.upload_service


@router.post(
    "/uploads",
    response_model=InitiateAssetUploadResponse,
    status_code=HTTPStatus.CREATED,
)
async def initiate_asset_upload(
    payload: InitiateAssetUploadRequest,
    request: Request,
    principal: Annotated[RequestPrincipal, Depends(request_principal)],
) -> InitiateAssetUploadResponse:
    try:
        initiated = await upload_service(request).initiate(
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
            file_name=payload.file_name,
            content_type=payload.content_type,
            size_bytes=payload.size_bytes,
            content_sha256=payload.content_sha256,
        )
    except UnsupportedMediaTypeError as error:
        raise ApiError(
            status_code=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            code="UNSUPPORTED_MEDIA_TYPE",
            message="This media type is not supported for ingestion",
            details={"content_type": str(error)},
        ) from error
    except UploadTooLargeError as error:
        raise ApiError(
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            code="UPLOAD_TOO_LARGE",
            message="The file exceeds the configured upload limit",
        ) from error
    except ObjectStoreError as error:
        raise _storage_unavailable(error) from error

    return InitiateAssetUploadResponse(
        request_id=get_request_id(request),
        **asdict(initiated),
    )


@router.post(
    "/{asset_id}/versions/{version_number}/complete",
    response_model=CompleteAssetUploadResponse,
)
async def complete_asset_upload(
    asset_id: Annotated[UUID, Path()],
    version_number: Annotated[int, Path(ge=1)],
    request: Request,
    principal: Annotated[RequestPrincipal, Depends(request_principal)],
) -> CompleteAssetUploadResponse:
    try:
        completed = await upload_service(request).complete(
            tenant_id=principal.tenant_id,
            owner_user_id=principal.user_id,
            asset_id=str(asset_id),
            version_number=version_number,
        )
    except UploadNotFoundError as error:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="UPLOAD_NOT_FOUND",
            message="The upload does not exist",
        ) from error
    except UploadNotReadyError as error:
        raise ApiError(
            status_code=HTTPStatus.CONFLICT,
            code="UPLOAD_NOT_READY",
            message="The uploaded object is not ready to complete",
            details={"state": str(error)},
        ) from error
    except UploadValidationError as error:
        raise ApiError(
            status_code=HTTPStatus.CONFLICT,
            code="UPLOAD_VALIDATION_FAILED",
            message="The uploaded object failed integrity validation",
            details=error.issues,
        ) from error
    except ObjectStoreError as error:
        raise _storage_unavailable(error) from error

    return CompleteAssetUploadResponse(
        request_id=get_request_id(request),
        **asdict(completed),
    )


def _storage_unavailable(_error: Exception) -> ApiError:
    return ApiError(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        code="OBJECT_STORAGE_UNAVAILABLE",
        message="Object storage is temporarily unavailable",
    )


def _is_safe_tenant_id(value: str) -> bool:
    return SAFE_TENANT_PATTERN.fullmatch(value) is not None
