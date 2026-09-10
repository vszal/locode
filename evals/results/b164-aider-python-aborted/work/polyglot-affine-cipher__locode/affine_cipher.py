import math

M = 26


def _check_coprime(a, b):
    if math.gcd(a, M) != 1:
        raise ValueError("a and m must be coprime.")


def _transform(text, a, b, decode=False):
    _check_coprime(a, b)
    out = []
    for ch in text:
        if ch.isalpha():
            i = ord(ch.lower()) - ord("a")
            if decode:
                # find modular inverse of a mod 26
                inv = next(x for x in range(1, M) if (a * x) % M == 1)
                i = (inv * (i - b)) % M
            else:
                i = (a * i + b) % M
            out.append(chr(i + ord("a")))
        elif ch.isdigit():
            out.append(ch)
    return "".join(out)


def encode(plain_text, a, b):
    _check_coprime(a, b)
    letters = _transform(plain_text, a, b)
    return " ".join(letters[i:i + 5] for i in range(0, len(letters), 5))


def decode(ciphered_text, a, b):
    return _transform(ciphered_text, a, b, decode=True)
