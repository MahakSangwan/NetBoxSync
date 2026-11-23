"""CA certificate settings shared by the NetBox and Diode connections (D14).

`requests` lets REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE replace the CA file a
session was given (verify=None per request is filled from the environment
before the session's own `verify` is consulted). Both pynetbox and the Diode
SDK's login use `requests`, so either variable silently overrides
NETBOX_CA_FILE and DIODE_CERT_FILE. We warn when they are set.
"""

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

OVERRIDING_ENV = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def ca_file_from_env(name: str) -> Path | None:
    """The CA file named by environment variable `name`, `~` expanded; must exist."""
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{name}={value}: no such file")
    return path


def warn_if_overridden(name: str) -> None:
    """Warn when a CA override variable would replace the CA file in `name`."""
    if not os.environ.get(name):
        return
    for var in OVERRIDING_ENV:
        if os.environ.get(var):
            log.warning(
                "%s is set, so requests uses it instead of %s; unset it, "
                "or make sure it also contains %s's certificate",
                var,
                name,
                name,
            )
