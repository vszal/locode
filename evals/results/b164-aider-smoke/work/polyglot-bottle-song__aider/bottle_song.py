def recite(start, take=1):
    """
    Generate the lyrics for the 'Ten Green Bottles' song.

    :param start: The number of bottles in the first verse.
    :param take: The number of consecutive verses to generate.
    :return: A list of strings representing the lyrics.
    """
    if start < 1 or take < 1:
        return []

    lyrics = []
    for i in range(take):
        current = start - i
        next_bottles = current - 1

        # Handle singular/plural and capitalization
        current_word = _number_to_word(current, capitalize=True)
        next_word = _number_to_word(next_bottles, capitalize=False)

        # Line 1 and 2 are the same
        line1 = f"{current_word} green bottle{'s' if current != 1 else ''} hanging on the wall,"
        line2 = line1

        # Line 3
        line3 = "And if one green bottle should accidentally fall,"

        # Line 4
        if next_bottles == 0:
            line4 = "There'll be no green bottles hanging on the wall."
        else:
            line4 = f"There'll be {next_word} green bottle{'s' if next_bottles != 1 else ''} hanging on the wall."

        lyrics.extend([line1, line2, line3, line4])

        # Add blank line between verses, but not after the last one
        if i < take - 1:
            lyrics.append("")

    return lyrics


def _number_to_word(n, capitalize=False):
    """
    Convert a number to its word representation for the song.
    """
    words = {
        0: "no",
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
    }
    word = words[n]
    if capitalize:
        return word.capitalize()
    return word
