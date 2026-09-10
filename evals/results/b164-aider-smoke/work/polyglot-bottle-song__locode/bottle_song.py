NUMBERS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def _verse(n):
    if n == 1:
        return [
            "One green bottle hanging on the wall,",
            "One green bottle hanging on the wall,",
            "And if one green bottle should accidentally fall,",
            "There'll be no green bottles hanging on the wall.",
        ]
    word = NUMBERS[n]
    next_n = n - 1
    next_word = NUMBERS[next_n]
    next_form = "green bottle" if next_n == 1 else "green bottles"
    return [
        f"{word.capitalize()} green bottles hanging on the wall,",
        f"{word.capitalize()} green bottles hanging on the wall,",
        "And if one green bottle should accidentally fall,",
        f"There'll be {next_word} {next_form} hanging on the wall.",
    ]


def recite(start, take=1):
    lines = []
    for i in range(take):
        if lines:
            lines.append("")
        lines.extend(_verse(start - i))
    return lines
