from tenderflow.api.problems import _response
from tenderflow.services.errors import ServiceError


class UploadLimits:
    def __init__(self, app, maximum: int):
        self.app = app
        self.maximum = maximum + 64 * 1024

    async def __call__(self, scope, receive, send):
        upload = (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"].startswith("/api/v1/tenders/")
            and scope["path"].endswith("/documents")
        )
        if not upload:
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        try:
            size = int(headers.get(b"content-length", b"0"))
        except ValueError:
            size = self.maximum + 1
        if size > self.maximum:
            await _response("upload_too_large")(scope, receive, send)
            return
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.maximum:
                raise ServiceError("upload_too_large")
            return message

        await self.app(scope, bounded_receive, send)
