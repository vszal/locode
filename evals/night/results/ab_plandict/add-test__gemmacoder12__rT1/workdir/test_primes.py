import pytest
from primes import is_prime

def test_is_prime_true():
    assert is_prime(2) is True
    assert is_prime(3) is True
    assert is_prime(5) is True
    assert is_prime(7) is True
    assert is_prime(11) is True
    assert is_prime(13) is True

def test_is_prime_false():
    assert is_prime(0) is False
    assert is_prime(1) is False
    assert is_prime(4) is False
    assert is_prime(6) is False
    assert is_prime(8) is False
    assert is_prime(9) is False
    assert is_prime(10) is False
    assert is_prime(15) is False
