"""Self-signed TLS certificates, so agents and the hub can serve HTTPS without an Ingress."""

import datetime
import ipaddress
import logging
import os
from pathlib import Path
from typing import Iterable, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

log = logging.getLogger(__name__)


def ensure_self_signed(directory: str, common_name: str, names: Iterable[str] = ()) -> Tuple[str, str]:
    """Return (cert_path, key_path), generating a 10-year self-signed pair on first use."""
    d = Path(directory)
    cert, key = d / "tls.crt", d / "tls.key"
    if cert.exists() and key.exists():
        return str(cert), str(key)
    d.mkdir(parents=True, exist_ok=True)
    private_key = ec.generate_private_key(ec.SECP256R1())
    sans = []
    for n in {"localhost", common_name, *names}:
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            sans.append(x509.DNSName(n))
    sans.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "k10y (self-signed)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    key.write_bytes(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    os.chmod(key, 0o600)
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    log.info("generated self-signed certificate %s", cert)
    return str(cert), str(key)
