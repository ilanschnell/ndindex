from collections.abc import Sequence
from itertools import product
from functools import reduce
from operator import mul

from .ndindex import ImmutableObject, operator_index, asshape, ndindex
from .tuple import Tuple
from .slice import Slice
from .integer import Integer
from .subindex_helpers import ceiling

# np.prod has overflow and math.prod is Python 3.8+ only
def prod(seq):
    return reduce(mul, seq, 1)

class ChunkSize(ImmutableObject, Sequence):
    """
    Represents a chunk size.

    A chunk size is a tuple of length n where each element is either a
    positive integer or None. It represents a chunking of an array with n
    dimensions, where each corresponding dimension is chunked by the
    corresponding chunk size, or not chunked for None.

    For example, given a 3 dimensional chunk size of (20, 20, None) and an
    array of shape (40, 30, 10), the array would be split into four chunks,
    corresponding to the indices `0:20,0:20,:`, `0:20,20:30,:`,
    `20:40,0:20,:`, and `20:40,20:30,:`. Note that the size of a chunk may be
    less than the total chunk size if the array shape is not a multiple of the
    chunk size in a given dimension.

    """
    def _typecheck(self, chunk_size):
        # TODO: Also accept ChunkSize(1, 2, 3)?
        if isinstance(chunk_size, Tuple):
            raise TypeError("Tuple is not a valid input to ChunkSize. Use tuple instead.")
        args = []
        for i in chunk_size:
            if i is None:
                raise NotImplementedError("None in chunks is not supported yet")
                # args.append(i)
            else:
                try:
                    i = operator_index(i)
                except TypeError:
                    raise TypeError("Chunks must be positive integers or None")
                if i <= 0:
                    raise ValueError("Chunks must be positive integers")
                args.append(i)
        return (tuple(args),)

    def __hash__(self):
        return hash(self.args[0])

    # Methods for collections.abc.Sequence to make ChunkSize act like a tuple
    def __getitem__(self, *args):
        return self.args[0].__getitem__(*args)

    def __len__(self):
        return len(self.args[0])

    def num_chunks(self, shape):
        """
        Give the number of chunks for the given shape.

        This is the same as `len(self.indices(shape))`, but much faster.
        """
        shape = asshape(shape)
        d = [ceiling(i, c) for i, c in zip(shape, self)]
        if 0 in d:
            return 1
        return prod(d)

    def indices(self, shape):
        """
        Yield a set of ndindex indices for the chunks on an array of shape `shape`.

        If the shape is not a multiple of the chunk size, some chunks will be
        truncated, so that `len(idx.args[i]) <ndindex.Slice.__len__>` can be
        used to get the size of an indexed axis.

        For example, if `a` has shape `(10, 19)` and is chunked into chunks
        of shape `(5, 5)`:

        >>> from ndindex.chunking import ChunkSize
        >>> chunk_size = ChunkSize((5, 5))
        >>> for idx in chunk_size.indices((10, 19)):
        ...     print(idx)
        Tuple(slice(0, 5, 1), slice(0, 5, 1))
        Tuple(slice(0, 5, 1), slice(5, 10, 1))
        Tuple(slice(0, 5, 1), slice(10, 15, 1))
        Tuple(slice(0, 5, 1), slice(15, 19, 1))
        Tuple(slice(5, 10, 1), slice(0, 5, 1))
        Tuple(slice(5, 10, 1), slice(5, 10, 1))
        Tuple(slice(5, 10, 1), slice(10, 15, 1))
        Tuple(slice(5, 10, 1), slice(15, 19, 1))

        """
        shape = asshape(shape)

        if len(shape) != len(self):
            raise ValueError("chunks dimensions must equal the array dimensions")
        d = [ceiling(i, c) for i, c in zip(shape, self)]
        if 0 in d:
            yield Tuple(*[Slice(0, bool(i)*chunk_size, 1) for i, chunk_size in zip(d, self)]).expand(shape)
        for p in product(*[range(i) for i in d]):
            # p = (0, 0, 0), (0, 0, 1), ...
            yield Tuple(*[Slice(chunk_size*i, min(chunk_size*(i + 1), n), 1)
                          for n, chunk_size, i in zip(shape, self, p)])

    def as_subchunks(self, idx, shape, *, _force_slow=False):
        """
        Split an index `idx` on an array of shape `shape` into subchunk indices.

        Yields tuples `(c, index)`, where `c` is an index for the chunk that
        should be sliced, and `index` is an index into that chunk giving the
        elements of `idx` that are included in it (`c` and `index` are both
        ndindex indices).

        That is to say, for each `(c, index)` pair yielded, `a[c][index]` will
        give those elements of `a[idx]` that are part of the `c` chunk.

        Note that this only yields those indices that are nonempty.

        >>> from ndindex.chunking import ChunkSize
        >>> idx = (slice(5, 15), 0)
        >>> shape = (20, 20)
        >>> chunk_size = ChunkSize((10, 10))
        >>> for c, index in chunk_size.as_subchunks(idx, shape):
        ...     print(c)
        ...     print('    ', index)
        Tuple(slice(0, 10, 1), slice(0, 10, 1))
            Tuple(slice(5, 10, 1), 0)
        Tuple(slice(10, 20, 1), slice(0, 10, 1))
            Tuple(slice(0, 5, 1), 0)

        """
        shape = asshape(shape)
        if len(shape) != len(self):
            raise ValueError("chunks dimensions must equal the array dimensions")

        if 0 in shape:
            return
        idx = ndindex(idx).expand(shape)

        # The slow naive fallback is kept here for testing purposes and to support
        # indices that aren't supported in the fast way yet below.
        def _fallback():
            for c in self.indices(shape):
                try:
                    index = idx.as_subindex(c)
                except ValueError:
                    continue

                if not index.isempty(self):
                    yield (c, index)
            return

        if _force_slow or len(idx.args) > len(self):
            yield from _fallback()
            return

        iters = []
        for i, n in zip(idx.args, self):
            if isinstance(i, Integer):
                iters.append([i.raw//n])
            elif isinstance(i, Slice) and i.step > 0:
                a, N, m = i.args
                if m >= n:
                    iters.append(((a + k*m)//n for k in range(ceiling(N, n))))
                else:
                    iters.append(range(ceiling(N, n)))
            else:
                # fallback to the naive algorithm
                yield from _fallback()
                return

        def _indices(iters):
            for p in product(*iters):
                # p = (0, 0, 0), (0, 0, 1), ...
                yield Tuple(*[Slice(chunk_size*i, min(chunk_size*(i + 1), n), 1)
                          for n, chunk_size, i in zip(shape, self, p)])

        for c in _indices(iters):
            # Empty indices should be impossible by the construction of the
            # iterators above.
            yield c, idx.as_subindex(c)
