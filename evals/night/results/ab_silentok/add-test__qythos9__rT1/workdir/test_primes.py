import pytest
from primes import is_prime

# Prime numbers
def test_is_prime_two():
    assert is_prime(2) is True

def test_is_prime_three():
    assert is_prime(3) is True

def test_is_prime_five():
    assert is_prime(5) is True

def test_is_prime_seventeen():
    assert is_prime(17) is True

def test_is_prime_eighty_three():
    assert is_prime(83) is True

# Non-prime numbers
def test_is_prime_one():
    assert is_prime(1) is False

def test_is_prime_zero():
    assert is_prime(0) is False

def test_is_prime_negative_one():
    assert is_prime(-1) is False

def test_is_prime_negative_ten():
    assert is_prime(-10) is False

def test_is_prime_four():
    assert is_prime(4) is False

def test_is_prime_nine():
    assert is_prime(9) is False

def test_is_prime_twelve():
    assert is_prime(12) is False

def test_is_prime_fifty_five():
    assert is_prime(55) is False