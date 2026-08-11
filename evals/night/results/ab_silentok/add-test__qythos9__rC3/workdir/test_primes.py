import pytest
from primes import is_prime

@pytest.mark.parametrize("n, expected", [
    (2, True),
    (3, True),
    (5, True),
    (7, True),
    (11, True),
    (13, True),
    (17, True),
    (19, True),
    (23, True),
    (0, False),
    (1, False),
    (4, False),
    (6, False),
    (8, False),
    (9, False),
    (10, False),
    (12, False),
    (14, False),
    (15, False),
    (16, False),
    (18, False),
    (20, False),
    (21, False),
    (22, False),
    (24, False),
    (25, False),
    (26, False),
    (27, False),
    (28, False),
    (30, False),
])
def test_is_prime(n, expected):
    assert is_prime(n) == expected