import re
from uuid import UUID

from tenderflow.services.errors import ServiceError


def etag(
    *, resource: str, resource_id: UUID, version: int, variant: str | None = None
) -> str:
    suffix = f"-{variant}" if variant is not None else ""
    return f'"{resource}-{resource_id}-{version}{suffix}"'


def expected_version(
    *,
    header: str | None,
    resource: str,
    resource_id: UUID,
    variant: str | None = None,
) -> int | None:
    if header is None:
        return None
    suffix = re.escape(f"-{variant}") if variant is not None else ""
    match = re.fullmatch(
        rf'"{re.escape(resource)}-{resource_id}-([1-9][0-9]{{0,18}}){suffix}"',
        header,
    )
    if match is None:
        raise ServiceError("precondition_failed")
    return int(match[1])
