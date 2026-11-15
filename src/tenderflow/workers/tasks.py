from procrastinate import RetryStrategy

from tenderflow.config import Settings


def retry_strategy(*, settings: Settings | None = None) -> RetryStrategy:
    settings = settings if settings is not None else Settings()
    return RetryStrategy(
        max_attempts=settings.job_max_retries,
        wait=1,
        retry_exceptions=(TimeoutError, ConnectionError),
    )
