"""Port of src/random.c and the ISO_RANDOMIZE macros from include/random.h."""

RAND_MULT = 1103515245


def iso_randomize1(value):
    return (RAND_MULT * value + 24691) & 0xFFFFFFFF


def iso_randomize2(value):
    return (RAND_MULT * value + 12345) & 0xFFFFFFFF


class Rng:
    """The game's main random number generator (gRngValue)."""

    def __init__(self, seed=0):
        self.value = seed & 0xFFFFFFFF

    def seed(self, seed):
        """SeedRng(u16 seed)"""
        self.value = seed & 0xFFFF

    def random(self):
        """Random(): returns a 16-bit pseudorandom number."""
        self.value = iso_randomize1(self.value)
        return self.value >> 16

    def random32(self):
        """Random32(): Random() | (Random() << 16).

        agbcc evaluates the operands left to right, so the first call
        supplies the low half."""
        lo = self.random()
        hi = self.random()
        return lo | (hi << 16)
