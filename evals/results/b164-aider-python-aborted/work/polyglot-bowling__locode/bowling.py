class BowlingGame:
    def __init__(self):
        self._rolls = []
        self._game_over = False

    def roll(self, pins):
        if self._game_over:
            raise Exception("Cannot roll after the game is over")
        if not 0 <= pins <= 10:
            raise Exception(f"Invalid roll: {pins}")
        if len(self._rolls) >= 21:
            raise Exception("Cannot roll after the game is over")
        if len(self._rolls) >= 20:
            if self._rolls[-1] != 10:
                raise Exception("Third bonus roll cannot be a strike")
            self._rolls.append(pins)
            self._game_over = True
            return
        if len(self._rolls) >= 10:
            if self._rolls[-1] != 10:
                raise Exception("Cannot roll after the game is over")
            self._rolls.append(pins)
            self._game_over = True
            return
        if self._rolls and self._rolls[-1] == 10:
            if len(self._rolls) >= 11:
                if self._rolls[-1] != 10:
                    raise Exception("Third bonus roll cannot be a strike")
                self._rolls.append(pins)
                self._game_over = True
                return
            if pins != 0:
                raise Exception("Second roll after strike must be 0")
            self._rolls.append(0)
            return
        if self._rolls and self._rolls[-1] != 10:
            if self._rolls[-1] + pins > 10:
                raise Exception(f"Frame total {self._rolls[-1] + pins} exceeds 10")
            self._rolls.append(pins)
            if self._rolls[-1] + self._rolls[-2] == 10 and len(self._rolls) >= 10:
                self._game_over = True
            return
        self._rolls.append(pins)
        if pins == 10 and len(self._rolls) == 10:
            self._game_over = True

    def score(self):
        if not self._game_over:
            raise Exception("Game is incomplete")
        total = 0
        i = 0
        while i < 10:
            if self._rolls[i] == 10:
                total += 10 + self._rolls[i + 1] + self._rolls[i + 2]
                i += 1
            else:
                total += self._rolls[i] + self._rolls[i + 1]
                if self._rolls[i] + self._rolls[i + 1] == 10:
                    total += self._rolls[i + 2]
                i += 2
        return total
