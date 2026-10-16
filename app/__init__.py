"""Windows AI Support Agent application bootstrap.

TLS is configured here because Python imports the package before importing
``app.main`` or any of the networking modules.  Corporate Windows devices may
trust an HTTPS-inspection CA in the Windows certificate store that certifi does
not know about.  truststore makes Python networking use the native Windows
trust store while keeping normal certificate verification enabled.
"""

from __future__ import annotations

TLS_TRUST_MODE = "python-default"
TLS_TRUST_ERROR: str | None = None

try:
    import truststore

    # This project is an application (not a reusable library), so early global
    # injection is appropriate. It must happen before requests/OpenAI import SSL.
    truststore.inject_into_ssl()
    TLS_TRUST_MODE = "windows-system-store"
except ImportError as exc:
    TLS_TRUST_ERROR = (
        "The 'truststore' package is not installed. Run "
        "'python -m pip install -r requirements.txt' before starting the app. "
        f"Details: {exc}"
    )
except Exception as exc:  # fail visibly later rather than disabling TLS checks
    TLS_TRUST_ERROR = f"Windows system trust-store initialization failed: {exc}"
