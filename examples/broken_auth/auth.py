"""Sample broken module for demo/testing the healer."""


def validate_token(token: str, secret: str) -> bool:
    # Bug: uses `=` instead of `==`
    return token = secret  # SyntaxError intentional for demo


def hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()


def check_password(password: str, hashed: str) -> bool:
    return hash_password(password) = hashed  # Bug: same issue
