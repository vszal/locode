def is_palindrome(s):
    s = s.lower()
    cleaned = [c for c in s if c.isalnum()]
    return cleaned == cleaned[::-1]
