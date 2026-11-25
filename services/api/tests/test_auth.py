"""Token verification unit tests (no database, no network)."""

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from jackson_api.auth import AuthError


def test_valid_token_accepted(verifier, make_token):
    assert verifier.verify(make_token("user_abc"))["sub"] == "user_abc"


def test_token_signed_by_other_key_rejected(verifier, make_token):
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthError):
        verifier.verify(make_token("user_abc", key=attacker))


def test_expired_token_rejected(verifier, make_token):
    with pytest.raises(AuthError):
        verifier.verify(make_token("user_abc", exp_in=-60))


def test_wrong_issuer_rejected(verifier, make_token):
    with pytest.raises(AuthError):
        verifier.verify(make_token("user_abc", issuer="https://evil.invalid"))


def test_wrong_authorized_party_rejected(verifier, make_token):
    with pytest.raises(AuthError):
        verifier.verify(make_token("user_abc", azp="https://evil.invalid"))


def test_missing_authorized_party_rejected(verifier, make_token):
    with pytest.raises(AuthError):
        verifier.verify(make_token("user_abc", azp=None))


def test_unsigned_token_rejected(verifier):
    import jwt

    token = jwt.encode({"sub": "user_abc"}, key=None, algorithm="none")
    with pytest.raises(AuthError):
        verifier.verify(token)


def test_hs256_key_confusion_rejected(verifier, rsa_key, make_token):
    """Classic attack: sign with HMAC using the public key as the secret."""
    import jwt

    token = jwt.encode({"sub": "user_abc", "iss": "https://test-issuer.invalid"},
                       "public-key-as-secret-padded-to-32-bytes!", algorithm="HS256")
    with pytest.raises(AuthError):
        verifier.verify(token)
