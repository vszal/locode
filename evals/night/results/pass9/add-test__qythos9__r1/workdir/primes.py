def is_prime(n: int) -> bool:
    """Return True if n is a prime number, False otherwise.
    Numbers less than 2 are not prime.
    """
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    limit = int(n ** 0.5) + 1
    for i in range(3, limit, 2):
        if n % i == 0:
            return False
    return True