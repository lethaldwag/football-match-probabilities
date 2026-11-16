"""Shared HTTP settings for provider adapters."""

import ssl
from functools import lru_cache

import truststore


@lru_cache
def tls_context() -> ssl.SSLContext:
    """Verify TLS against the operating system's trust store.

    Some provider sites serve certificate chains that the bundled `certifi` store cannot validate;
    the OS store can. Certificate verification is never disabled.
    """
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
