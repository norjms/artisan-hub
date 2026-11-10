"""HUB_OIDC_CA_PEM: trusting a private authority for Authentik, besides the system's."""

import datetime as dt
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _ca_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test private authority")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_without_a_private_authority_the_system_ones_are_used(monkeypatch):
    from app.auth import oidc_verify
    from app.config import settings
    monkeypatch.setattr(settings(), "oidc_ca_pem", "")
    assert oidc_verify() is True


def test_a_private_authority_is_trusted_alongside_the_system_ones(monkeypatch):
    from app.auth import oidc_verify
    from app.config import settings
    pem = _ca_pem()
    monkeypatch.setattr(settings(), "oidc_ca_pem", pem)
    ctx = oidc_verify()
    assert isinstance(ctx, ssl.SSLContext)
    subjects = [dict(x[0] for x in c["subject"]).get("commonName") for c in ctx.get_ca_certs()]
    assert "Test private authority" in subjects
    assert len(subjects) > 1, "the system's authorities must still be trusted"
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
