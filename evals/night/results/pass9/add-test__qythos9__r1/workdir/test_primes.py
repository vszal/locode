import pytest
from primes import is_prime

# Prime numbers
class TestIsPrimePrimes:
    def test_two(self):
        assert is_prime(2) is True
    
    def test_three(self):
        assert is_prime(3) is True
    
    def test_five(self):
        assert is_prime(5) is True
    
    def test_seventeen(self):
        assert is_prime(17) is True
    
    def test_eightythree(self):
        assert is_prime(83) is True

# Non-prime numbers
class TestIsPrimeNonPrimes:
    def test_one(self):
        assert is_prime(1) is False
    
    def test_zero(self):
        assert is_prime(0) is False
    
    def test_negative(self):
        assert is_prime(-5) is False
    
    def test_four(self):
        assert is_prime(4) is False
    
    def test_nine(self):
        assert is_prime(9) is False
    
    def test_twelve(self):
        assert is_prime(12) is False