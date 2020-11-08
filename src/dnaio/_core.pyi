from typing import Optional, Tuple, Union, Iterable, BinaryIO

class Sequence:
    name: str
    sequence: str
    qualities: Optional[str]
    def __init__(self, name: str, sequence: str, qualities: Optional[str] = ...) -> None: ...
    def __getitem__(self, s: slice) -> Sequence: ...
    def __repr__(self) -> str: ...
    def __len__(self) -> int: ...
    def __richcmp__(self, other: Sequence, op: int) -> bool: ...
    def fastq_bytes(self) -> bytes: ...
    def fastq_bytes_two_headers(self) -> bytes: ...

def paired_fastq_heads(buf1: Union[bytes,bytearray], buf2: Union[bytes,bytearray], end1: int, end2: int) -> Tuple[int, int]: ...
# TODO Sequence should be sequence_class, first yielded value is a bool
def fastq_iter(file: BinaryIO, sequence_class, buffer_size: int) -> Iterable[Sequence]: ...
def record_names_match(header1: str, header2: str) -> bool: ...
