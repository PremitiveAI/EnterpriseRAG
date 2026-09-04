"""Machine-readable error codes (spec §38, docs/api/error-codes.md).

Codes are permanent. Adding one is safe; renaming one is a breaking change.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    # --- Auth ---
    UNAUTHORIZED = "UNAUTHORIZED"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    OTP_ATTEMPTS_EXCEEDED = "OTP_ATTEMPTS_EXCEEDED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    DOCUMENT_NOT_PUBLISHABLE = "DOCUMENT_NOT_PUBLISHABLE"

    # --- Upload / validation ---
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    LEGACY_FORMAT_UNSUPPORTED = "LEGACY_FORMAT_UNSUPPORTED"
    INVALID_MIME_TYPE = "INVALID_MIME_TYPE"
    FILE_SIGNATURE_MISMATCH = "FILE_SIGNATURE_MISMATCH"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_EMPTY = "FILE_EMPTY"
    INVALID_FILE = "INVALID_FILE"
    INVALID_FILENAME = "INVALID_FILENAME"
    NO_FILES_PROVIDED = "NO_FILES_PROVIDED"
    TOO_MANY_FILES = "TOO_MANY_FILES"

    # --- Duplicates ---
    DUPLICATE_DOCUMENT = "DUPLICATE_DOCUMENT"
    DUPLICATE_IN_BATCH = "DUPLICATE_IN_BATCH"
    CONTENT_DUPLICATE = "CONTENT_DUPLICATE"  # async only, never an HTTP error

    # --- Documents ---
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    DOCUMENT_ALREADY_DELETED = "DOCUMENT_ALREADY_DELETED"
    DOCUMENT_NOT_EDITABLE = "DOCUMENT_NOT_EDITABLE"
    DOCUMENT_NOT_REPROCESSABLE = "DOCUMENT_NOT_REPROCESSABLE"
    STORAGE_FILE_MISSING = "STORAGE_FILE_MISSING"

    # --- Categories (Super Admin taxonomy) ---
    CATEGORY_NOT_FOUND = "CATEGORY_NOT_FOUND"
    CATEGORY_SLUG_TAKEN = "CATEGORY_SLUG_TAKEN"
    # Its own code rather than a generic 422: the caller is not malformed, they
    # are asking for something the system refuses on purpose, and the message
    # has to explain why (existing classifications point at the old slug).
    CATEGORY_SLUG_IMMUTABLE = "CATEGORY_SLUG_IMMUTABLE"

    # --- Agent rules (Super Admin prompt editing, ADR-009) ---
    # A READ failure has no code on purpose: a missing or unreadable rule file
    # is not an error the caller sees, it is the in-code prompt being used.
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_RULE_INVALID = "AGENT_RULE_INVALID"
    AGENT_RULE_WRITE_FAILED = "AGENT_RULE_WRITE_FAILED"

    # --- Processing (recorded on the document, not returned to a client) ---
    PROCESSING_FAILED = "PROCESSING_FAILED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    OCR_FAILED = "OCR_FAILED"
    OCR_UNAVAILABLE = "OCR_UNAVAILABLE"
    CLASSIFICATION_FAILED = "CLASSIFICATION_FAILED"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    VECTOR_INDEXING_FAILED = "VECTOR_INDEXING_FAILED"
    NO_TEXT_EXTRACTED = "NO_TEXT_EXTRACTED"
    RETRY_LIMIT_EXCEEDED = "RETRY_LIMIT_EXCEEDED"

    # --- Chat / retrieval ---
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    MESSAGE_EMPTY = "MESSAGE_EMPTY"
    MESSAGE_TOO_LONG = "MESSAGE_TOO_LONG"
    SEARCH_FAILED = "SEARCH_FAILED"
    AI_PROCESSING_FAILED = "AI_PROCESSING_FAILED"
    AI_TIMEOUT = "AI_TIMEOUT"
    NO_RELEVANT_CONTEXT = "NO_RELEVANT_CONTEXT"  # accompanies a 200

    # --- LLM provider configuration (ADR-010) ---
    # Both are 503 and both mean "no answer is possible right now", but they
    # point at different fixes: the first is a Super Admin who has not chosen a
    # provider, the second is a database that cannot be read.
    LLM_NO_ACTIVE_PROVIDER = "LLM_NO_ACTIVE_PROVIDER"
    LLM_CONFIG_UNAVAILABLE = "LLM_CONFIG_UNAVAILABLE"
    LLM_PROVIDER_NOT_FOUND = "LLM_PROVIDER_NOT_FOUND"
    # The pre-flight call failed. Nothing was written.
    LLM_PROVIDER_TEST_FAILED = "LLM_PROVIDER_TEST_FAILED"
    # The credential changed between the test and the commit, so what was
    # verified is not what would have been activated.
    LLM_PROVIDER_CHANGED = "LLM_PROVIDER_CHANGED"
    # Deleting the row that is currently answering. Activate another first.
    LLM_PROVIDER_ACTIVE = "LLM_PROVIDER_ACTIVE"

    # --- Infrastructure ---
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
