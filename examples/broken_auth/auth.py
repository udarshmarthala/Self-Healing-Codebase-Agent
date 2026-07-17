"""Sample broken module for demo — healer should fix the logic bugs."""


def validate_token(token: str, secret: str) -> bool:
    # Bug: != should be ==
    return token != secret


def hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def check_password(password: str, hashed: str) -> bool:
    # Bug: != should be ==
    return hash_password(password) != hashed
