from src.auth.security import hash_opaque_token, hash_password, new_opaque_token, verify_password


def test_password_hash_round_trips() -> None:
    hashed = hash_password("correct-horse-1")

    assert verify_password("correct-horse-1", hashed) is True
    assert verify_password("wrong-horse-1", hashed) is False


def test_same_password_hashes_differently() -> None:
    """Salted: equal passwords must not produce equal rows."""
    assert hash_password("correct-horse-1") != hash_password("correct-horse-1")


def test_verify_password_never_raises_on_garbage_hash() -> None:
    assert verify_password("anything", "not-an-argon2-hash") is False


def test_opaque_token_is_stored_only_as_its_hash() -> None:
    raw, hashed = new_opaque_token()

    assert raw != hashed
    assert hash_opaque_token(raw) == hashed
    assert len(raw) >= 43  # 32 bytes, url-safe base64
