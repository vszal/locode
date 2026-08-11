import pytest
from primes import is_prime

@pytest.mark.parametrize('n', [2, 3, 5, 7, 11, 13, 17, 19, 23])
def test_is_prime_true(n):
    assert is_prime(n) is True

@pytest.mark.parametrize('n', [0, 1, 4, 6, 8, 9, 10, 12, 14, 15, 16, 18, 20, 21, 22])
def test_is_prime_false(n):
    assert is_prime(n) is False