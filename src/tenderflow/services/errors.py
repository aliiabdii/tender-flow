class ServiceError(Exception):
    def __init__(self, code: str, *, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
