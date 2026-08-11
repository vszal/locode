import pytest
from primes import is_prime

@pytest.mark.parametrize("test_is_prime", [
    (2, True),
    (3, True),
    (5, True),
    (7, True),
    (11, True),
    (4, False),
    (6, False),
    (9, False),
    (10, False),
    (1, False),
    (0, False),
    (-5, False),
])
def test_is_prime(n, expected):
    assert is_prime(n) == expected