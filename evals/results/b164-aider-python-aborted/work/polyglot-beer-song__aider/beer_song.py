def recite(start, take=1):
    verses = []
    for i in range(take):
        n = start - i
        if n > 1:
            line1 = f"{n} bottles of beer on the wall, {n} bottles of beer."
            next_n = n - 1
            if next_n == 1:
                line2 = "Take one down and pass it around, 1 bottle of beer on the wall."
            else:
                line2 = f"Take one down and pass it around, {next_n} bottles of beer on the wall."
        elif n == 1:
            line1 = "1 bottle of beer on the wall, 1 bottle of beer."
            line2 = "Take it down and pass it around, no more bottles of beer on the wall."
        else:
            line1 = "No more bottles of beer on the wall, no more bottles of beer."
            line2 = "Go to the store and buy some more, 99 bottles of beer on the wall."
        
        verses.append(line1)
        verses.append(line2)
        
        if i < take - 1:
            verses.append("")
            
    return verses
