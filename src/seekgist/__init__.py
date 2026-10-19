"""SeekGist Python SDK: Arabic-first e-commerce search (search, suggest, catalog sync, webhooks).

from seekgist import SeekGist
client = SeekGist(api_key="sg_sec_...")          # or set SEEKGIST_API_KEY
res = client.find("فستان سهرة تحت 100")
print(res.data["ids"], res.usage)
"""

from ._async_client import AsyncSeekGist
from ._client import DEFAULT_BASE_URL, SeekGist, __version__
from ._generated.api import GENERATOR_VERSION, OPS, SPEC_SHA256
from ._spec import ApiResponse, FileInput, OperationSpec, RateLimit, RequestOptions, Usage
from ._transport import new_idempotency_key, parse_usage
from ._upsert import MAX_BATCH, BatchInfo, UpsertSummary
from .errors import ApiError, ConnectionError, SeekGistError, TimeoutError, WebhookVerificationError
from .suggest import AsyncSuggester
from .webhooks import TOLERANCE_S, WebhookEvent, sign_webhook, verify_webhook

__all__ = [
    "DEFAULT_BASE_URL",
    "GENERATOR_VERSION",
    "MAX_BATCH",
    "OPS",
    "SPEC_SHA256",
    "TOLERANCE_S",
    "ApiError",
    "ApiResponse",
    "AsyncSeekGist",
    "AsyncSuggester",
    "BatchInfo",
    "ConnectionError",
    "FileInput",
    "OperationSpec",
    "RateLimit",
    "RequestOptions",
    "SeekGist",
    "SeekGistError",
    "TimeoutError",
    "UpsertSummary",
    "Usage",
    "WebhookEvent",
    "WebhookVerificationError",
    "__version__",
    "new_idempotency_key",
    "parse_usage",
    "sign_webhook",
    "verify_webhook",
]
