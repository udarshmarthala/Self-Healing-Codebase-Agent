from auth import validate_token, check_password


def test_valid_token():
    assert validate_token("secret123", "secret123") is True


def test_invalid_token():
    assert validate_token("wrong", "secret123") is False


def test_check_password():
    assert check_password("mypassword", check_password.__doc__ or "") is False
