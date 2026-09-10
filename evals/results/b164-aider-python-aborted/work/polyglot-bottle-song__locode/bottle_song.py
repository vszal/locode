NUMBERS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    0: "no",
}


def recite(start, take=1):
    lines = []
    for i in range(take):
        n = start - i
        next_n = n - 1
        word = NUMBERS[n]
        next_word = NUMBERS[next_n]
        bottle = "bottle" if n == 1 else "bottles"
        next_bottle = "bottle" if next_n == 1 else "bottles"
        lines.extend([
            f"{word.capitalize()} green {bottle} hanging on the wall,",
            f"{word.capitalize()} green {bottle} hanging on the wall,",
            f"And if one green bottle should accidentally fall,",
            f"There'll be {next_word} green {next_bottle} hanging on the wall.",
        ])
        if i < take - 1:
            lines.append("")
    return lines
