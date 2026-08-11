import pytest
from primes import is_prime

# Tests for prime numbers
def test_is_prime_two():
    assert is_prime(2) is True

def test_is_prime_three():
    assert is_prime(3) is True

def test_is_prime_five():
    assert is_prime(5) is True

def test_is_prime_seventeen():
    assert is_prime(17) is True

def test_is_prime_eightythree():
    assert is_prime(83) is True

# Tests for non-prime numbers
def test_is_prime_one():
    assert is_prime(1) is False

def test_is_prime_zero():
    assert is_prime(0) is False

def test_is_prime_negative():
    assert is_prime(-5) is False

def test_is_prime_four():
    assert is_prime(4) is False

def test_is_prime_nine():
    assert is_prime(9) is False

def test_is_prime_twelve():
    assert is_prime(12) is False

def test_is_prime_eightyfour():
    assert is_prime(84) is False