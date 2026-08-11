import pytest
from primes import is_prime

# Test cases for prime numbers
class TestIsPrime:
    def test_small_primes(self):
        assert is_prime(2) is True
        assert is_prime(3) is True
        assert is_prime(5) is True
        assert is_prime(7) is True
        assert is_prime(11) is True
        assert is_prime(13) is True

    def test_non_primes(self):
        assert is_prime(0) is False
        assert is_prime(1) is False
        assert is_prime(4) is False
        assert is_prime(6) is False
        assert is_prime(8) is False
        assert is_prime(9) is False
        assert is_prime(10) is False
        assert is_prime(12) is False
        assert is_prime(14) is False
        assert is_prime(15) is False
        assert is_prime(16) is False
        assert is_prime(18) is False
        assert is_prime(20) is False

    def test_negative_numbers(self):
        assert is_prime(-1) is False
        assert is_prime(-2) is False
        assert is_prime(-100) is False

    def test_larger_primes(self):
        assert is_prime(17) is True
        assert is_prime(19) is True
        assert is_prime(23) is True
        assert is_prime(29) is True
        assert is_prime(31) is True
        assert is_prime(37) is True
        assert is_prime(41) is True
        assert is_prime(43) is True
        assert is_prime(47) is True
        assert is_prime(53) is True

    def test_larger_non_primes(self):
        assert is_prime(21) is False
        assert is_prime(22) is False
        assert is_prime(24) is False
        assert is_prime(25) is False
        assert is_prime(26) is False
        assert is_prime(27) is False
        assert is_prime(28) is False
        assert is_prime(30) is False
        assert is_prime(32) is False
        assert is_prime(33) is False
        assert is_prime(34) is False
        assert is_prime(35) is False
        assert is_prime(36) is False
        assert is_prime(38) is False
        assert is_prime(39) is False
        assert is_prime(40) is False
        assert is_prime(42) is False
        assert is_prime(44) is False
        assert is_prime(45) is False
        assert is_prime(46) is False
        assert is_prime(48) is False
        assert is_prime(49) is False
        assert is_prime(50) is False
        assert is_prime(51) is False
        assert is_prime(52) is False
        assert is_prime(54) is False
        assert is_prime(55) is False
        assert is_prime(56) is False
        assert is_prime(57) is False
        assert is_prime(58) is False
        assert is_prime(60) is False
        assert is_prime(62) is False
        assert is_prime(63) is False
        assert is_prime(64) is False
        assert is_prime(65) is False
        assert is_prime(66) is False
        assert is_prime(68) is False
        assert is_prime(69) is False
        assert is_prime(70) is False
        assert is_prime(72) is False
        assert is_prime(74) is False
        assert is_prime(75) is False
        assert is_prime(76) is False
        assert is_prime(77) is False
        assert is_prime(78) is False
        assert is_prime(80) is False
        assert is_prime(81) is False
        assert is_prime(82) is False
        assert is_prime(84) is False
        assert is_prime(85) is False
        assert is_prime(86) is False
        assert is_prime(87) is False
        assert is_prime(88) is False
        assert is_prime(90) is False
        assert is_prime(91) is False
        assert is_prime(92) is False
        assert is_prime(93) is False
        assert is_prime(94) is False
        assert is_prime(95) is False
        assert is_prime(96) is False
        assert is_prime(98) is False
        assert is_prime(99) is False
        assert is_prime(100) is False