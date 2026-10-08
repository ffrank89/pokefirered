# Python port of gbagfx huff.c
# Huffman compression compatible with the GBA BIOS HuffUnComp routine.
#
# The tree construction mirrors the C implementation exactly (including its
# stable merge sort and node copying), so the output is byte-identical.

from util import fatal_error


class HuffNode:
    __slots__ = ("value", "is_leaf", "key", "left", "right")

    def __init__(self, value=0, is_leaf=False, key=0, left=None, right=None):
        self.value = value
        self.is_leaf = is_leaf
        self.key = key
        self.left = left
        self.right = right

    def copy(self):
        return HuffNode(self.value, self.is_leaf, self.key, self.left, self.right)


def _stable_sort(nodes):
    # msort() is a stable merge sort keyed on the node value.
    return sorted(nodes, key=lambda n: n.value)


def _write_tree(dest, tree, nitems, encoding):
    total = 2 * nitems - 1
    traversal = [None] * total
    index_of = {}

    traversal[0] = tree.copy()
    index_of[id(traversal[0])] = 0
    i = 1

    depth = 1
    while i < total:
        j = 0
        while i < total and j < (1 << depth):
            curr = traversal[0]
            parent = None
            k = 0
            while k < depth:
                if curr.is_leaf:
                    break
                parent = curr
                if (j >> (depth - k - 1)) & 1:
                    curr = curr.right
                else:
                    curr = curr.left
                k += 1
            if k == depth:
                if i - index_of[id(parent)] > 128:
                    fatal_error("Fatal error while compressing Huff file: unable to encode binary tree.\n")
                node = curr.copy()
                traversal[i] = node
                index_of[id(node)] = i
                if parent is not None:
                    if (j & 1) == 1:
                        parent.right = node
                    else:
                        parent.left = node
                if node.is_leaf:
                    encoding[node.key] = (depth & 0x3F, j)
                i += 1
            j += 1
        depth += 1

    dest[4] = (nitems - 1) & 0xFF

    for i in range(total):
        node = traversal[i]
        if node.is_leaf:
            dest[5 + i] = node.key
        else:
            right_index = index_of[id(node.right)]
            value = (int((right_index - i) / 2) - 1) & 0xFF
            if node.left.is_leaf:
                value |= 0x80
            if node.right.is_leaf:
                value |= 0x40
            dest[5 + i] = value


def huff_compress(src, bit_depth):
    src_size = len(src)
    if src_size <= 0:
        fatal_error("Fatal error while compressing Huff file.\n")

    nitems = 1 << bit_depth
    freqs = [HuffNode(0, True, i) for i in range(nitems)]
    encoding = [(0, 0)] * nitems

    if bit_depth == 8:
        for b in src:
            freqs[b].value += 1
    else:
        for b in src:
            freqs[b >> 4].value += 1
            freqs[b & 0xF].value += 1

    freqs = _stable_sort(freqs)

    # Prune zero-frequency values.
    for i in range(nitems):
        if freqs[i].value != 0:
            if i > 0:
                freqs = freqs[i:]
                nitems -= i
            break
        if i == nitems - 1:
            fatal_error("Fatal error while compressing Huff file.\n")

    # Iteratively collapse the two least frequent nodes.
    active = freqs
    for i in range(nitems - 1):
        left = active[0]
        right = active[1]
        tree_a = right.copy()
        tree_b = left.copy()
        branch = HuffNode(tree_a.value + tree_b.value, False, 0, tree_a, tree_b)
        active = active[2:] + [branch]
        if i < nitems - 2:
            active = _stable_sort(active)

    dest = bytearray(5 + 2 * nitems - 1)
    _write_tree(dest, active[0], nitems, encoding)

    dest_pos = 4 + nitems * 2
    del dest[dest_pos:]
    dest_buf = 0
    dest_bits = 0

    padded = bytes(src) + bytes((-src_size) % 4)
    mask = 0xFF >> (8 - bit_depth)
    out = dest

    for src_pos in range(0, src_size, 4):
        src_buf = padded[src_pos] | (padded[src_pos + 1] << 8) | (padded[src_pos + 2] << 16) | (padded[src_pos + 3] << 24)
        for _ in range(32 // bit_depth):
            nbits, bitstring = encoding[src_buf & mask]
            bitstring &= 0xFFFFFFFF
            if dest_bits + nbits >= 32:
                diff = dest_bits + nbits - 32
                dest_buf = (dest_buf << ((nbits - diff) % 32)) & 0xFFFFFFFF
                dest_buf |= bitstring >> diff
                bitstring &= ~(1 << diff) & 0xFFFFFFFF
                nbits = diff
                out += dest_buf.to_bytes(4, "little")
                dest_buf = 0
                dest_bits = 0
            if nbits != 0:
                dest_buf = ((dest_buf << nbits) | bitstring) & 0xFFFFFFFF
                dest_bits += nbits
            src_buf >>= bit_depth

    if dest_bits != 0:
        out += dest_buf.to_bytes(4, "little")

    out[0] = bit_depth | 0x20
    out[1] = src_size & 0xFF
    out[2] = (src_size >> 8) & 0xFF
    out[3] = (src_size >> 16) & 0xFF

    compressed_size = (len(out) + 3) & ~3
    return bytes(out).ljust(compressed_size, b"\0")


def huff_decompress(src):
    src_size = len(src)
    if src_size < 4:
        fatal_error("Fatal error while decompressing Huff file.\n")

    bit_depth = src[0] & 15
    if bit_depth != 4 and bit_depth != 8:
        fatal_error("Fatal error while decompressing Huff file.\n")

    dest_size = (src[3] << 16) | (src[2] << 8) | src[1]
    dest = bytearray()
    src = bytes(src) + b"\0" * 4

    tree_pos = 5
    tree_size = (src[4] + 1) * 2
    src_pos = 4 + tree_size
    cur_val_pos = 0
    dest_tmp = 0

    while True:
        if src_pos >= src_size:
            fatal_error("Fatal error while decompressing Huff file.\n")
        window = int.from_bytes(src[src_pos:src_pos + 4], "little")
        src_pos += 4
        for _ in range(32):
            cur_bit = (window >> 31) & 1
            tree_view = src[tree_pos]
            is_leaf = ((tree_view << cur_bit) & 0x80) != 0
            tree_pos &= ~1
            tree_pos += ((tree_view & 0x3F) + 1) * 2 + cur_bit
            if is_leaf:
                dest_tmp >>= bit_depth
                dest_tmp |= (src[tree_pos] << (32 - bit_depth)) & 0xFFFFFFFF
                cur_val_pos += 1
                if cur_val_pos == 32 // bit_depth:
                    dest += dest_tmp.to_bytes(4, "little")
                    dest_tmp = 0
                    cur_val_pos = 0
                    if len(dest) == dest_size:
                        return bytes(dest)
                tree_pos = 5
            window = (window << 1) & 0xFFFFFFFF
