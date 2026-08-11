import pytest
from primes import is_prime


class TestIsPrime:
    def test_small_primes(self):
        assert is_prime(2) is True
        assert is_prime(3) is True
        assert is_prime(5) is True
        assert is_prime(7) is True

    def test_small_non_primes(self):
        assert is_prime(0) is False
        assert is_prime(1) is False
        assert is_prime(4) is False
        assert is_prime(6) is False
        assert is_prime(9) is False
        assert is_prime(10) is False

    def test_larger_primes(self):
        assert is_prime(11) is True
        assert is_prime(13) is True
        assert is_prime(17) is True
        assert is_prime(19) is True
        assert is_prime(23) is True

    def test_larger_non_primes(self):
        assert is_prime(15) is False
        assert is_prime(21) is False
        assert is_prime(25) is False
        assert is_prime(27) is False
        assert is_prime(33) is False