import math


def encode(plain_text, a, b):
    m = 26
    if math.gcd(a, m) != 1:
        raise ValueError("a and m must be coprime.")

    filtered = [c for c in plain_text if c.isalnum()]
    encoded_chars = []
    for c in filtered:
        if c.isalpha():
            i = ord(c.lower()) - ord('a')
            encrypted = (a * i + b) % m
            encoded_chars.append(chr(ord('a') + encrypted))
        else:
            encoded_chars.append(c)

    encoded_str = ''.join(encoded_chars)
    groups = [encoded_str[i:i+5] for i in range(0, len(encoded_str), 5)]
    return ' '.join(groups)


def decode(ciphered_text, a, b):
    m = 26
    if math.gcd(a, m) != 1:
        raise ValueError("a and m must be coprime.")

    # Find modular multiplicative inverse of a mod m
    a_inv = None
    for x in range(1, m):
        if (a * x) % m == 1:
            a_inv = x
            break

    if a_inv is None:
        raise ValueError("a and m must be coprime.")

    filtered = [c for c in ciphered_text if c.isalnum()]
    decoded_chars = []
    for c in filtered:
        if c.isalpha():
            y = ord(c.lower()) - ord('a')
            decrypted = (a_inv * (y - b)) % m
            decoded_chars.append(chr(ord('a') + decrypted))
        else:
            decoded_chars.append(c)

    return ''.join(decoded_chars)
