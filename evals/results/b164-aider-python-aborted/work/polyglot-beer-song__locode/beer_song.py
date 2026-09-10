def recite(start, take=1):
    lines = []
    for i in range(take):
        n = start - i
        if n > 1:
            lines.append(f"{n} bottles of beer on the wall, {n} bottles of beer.")
            next_n = n - 1
            next_word = "bottle" if next_n == 1 else "bottles"
            next_phrase = f"{next_n} {next_word}" if next_n > 0 else "no more bottles"
            lines.append(f"Take one down and pass it around, {next_phrase} of beer on the wall.")
        elif n == 1:
            lines.append("1 bottle of beer on the wall, 1 bottle of beer.")
            lines.append("Take it down and pass it around, no more bottles of beer on the wall.")
        else:
            lines.append("No more bottles of beer on the wall, no more bottles of beer.")
            lines.append("Go to the store and buy some more, 99 bottles of beer on the wall.")
        if i < take - 1:
            lines.append("")
    return lines
