import pytest
from primes import is_prime

# Prime numbers
class TestPrimeNumbers:
    def test_two_is_prime(self):
        assert is_prime(2) is True
    
    def test_three_is_prime(self):
        assert is_prime(3) is True
    
    def test_five_is_prime(self):
        assert is_prime(5) is True
    
    def test_seventeen_is_prime(self):
        assert is_prime(17) is True
    
    def test_eighty_three_is_prime(self):
        assert is_prime(83) is True

# Non-prime numbers
class TestNonPrimeNumbers:
    def test_one_is_not_prime(self):
        assert is_prime(1) is False
    
    def test_zero_is_not_prime(self):
        assert is_prime(0) is False
    
    def test_negative_is_not_prime(self):
        assert is_prime(-5) is False
    
    def test_four_is_not_prime(self):
        assert is_prime(4) is False
    
    def test_nine_is_not_prime(self):
        assert is_prime(9) is False
    
    def test_twelve_is_not_prime(self):
        assert is_prime(12) is False