import os
import shutil
from io import BytesIO
from tempfile import mkdtemp
from textwrap import dedent

from pytest import raises, mark

import dnaio
from dnaio import (
    sequence_names_match, head, fastq_head, two_fastq_heads, find_fastq_record_end,
    read_paired_chunks, read_chunks_from_file, Sequence, ColorspaceSequence, FormatError, FastaReader, FastqReader, FastaQualReader, InterleavedSequenceReader,
    FastaWriter, FastqWriter, InterleavedSequenceWriter)


# files tests/data/simple.fast{q,a}
simple_fastq = [
    Sequence("first_sequence", "SEQUENCE1", ":6;;8<=:<"),
    Sequence("second_sequence", "SEQUENCE2", "83<??:(61")
    ]

simple_fasta = [Sequence(x.name, x.sequence, None) for x in simple_fastq]


class TestSequence:
    def test_too_many_qualities(self):
        with raises(FormatError):
            Sequence(name="name", sequence="ACGT", qualities="#####")

    def test_too_many_qualities_colorspace(self):
        with raises(FormatError):
            ColorspaceSequence(name="name", sequence="T0123", qualities="#####")

    def test_invalid_primer(self):
        with raises(FormatError):
            ColorspaceSequence(name="name", sequence="K0123", qualities="####")


class TestFastaReader:
    def test_file(self):
        with FastaReader("tests/data/simple.fasta") as f:
            reads = list(f)
        assert reads == simple_fasta

    def test_stringio(self):
        fasta = BytesIO(b">first_sequence\nSEQUENCE1\n>second_sequence\nSEQUENCE2\n")
        reads = list(FastaReader(fasta))
        assert reads == simple_fasta

    def test_with_comments(self):
        fasta = BytesIO(dedent(
            """
            # a comment
            # another one
            >first_sequence
            SEQUENCE1
            >second_sequence
            SEQUENCE2
            """).encode())
        reads = list(FastaReader(fasta))
        assert reads == simple_fasta

    def test_wrong_format(self):
        fasta = BytesIO(dedent(
            """
            # a comment
            # another one
            unexpected
            >first_sequence
            SEQUENCE1
            >second_sequence
            SEQUENCE2
            """).encode())
        with raises(FormatError):
            reads = list(FastaReader(fasta))

    def test_fastareader_keeplinebreaks(self):
        with FastaReader("tests/data/simple.fasta", keep_linebreaks=True) as f:
            reads = list(f)
        assert reads[0] == simple_fasta[0]
        assert reads[1].sequence == 'SEQUEN\nCE2'

    def test_context_manager(self):
        filename = "tests/data/simple.fasta"
        with open(filename, 'rb') as f:
            assert not f.closed
            reads = list(dnaio.open(f))
            assert not f.closed
        assert f.closed

        with FastaReader(filename) as sr:
            tmp_sr = sr
            assert not sr._file.closed
            reads = list(sr)
            assert not sr._file.closed
        assert tmp_sr._file is None
        # Open it a second time
        with FastaReader(filename) as sr:
            pass


class TestFastqReader:
    def test_fastqreader(self):
        with FastqReader("tests/data/simple.fastq") as f:
            reads = list(f)
        assert reads == simple_fastq

    def test_fastqreader_dos(self):
        with open('tests/data/dos.fastq', 'rb') as f:
            assert b'\r\n' in f.read()
        with FastqReader("tests/data/dos.fastq") as f:
            dos_reads = list(f)
        with FastqReader("tests/data/small.fastq") as f:
            unix_reads = list(f)
        assert dos_reads == unix_reads

    def test_fastq_wrongformat(self):
        with FastqReader("tests/data/withplus.fastq") as f:
            with raises(FormatError):
                reads = list(f)

    @mark.parametrize("length", range(1, len(b'@name\nACGT+\nHHHH\n')))
    def test_fastq_incomplete(self, length):
        full_fastq = b'@name\nACGT+\nHHHH\n'
        fastq = BytesIO(full_fastq[:length])
        with FastqReader(fastq) as fq:
            with raises(FormatError):
                list(fq)

    def test_context_manager(self):
        filename = "tests/data/simple.fastq"
        with open(filename) as f:
            assert not f.closed
            reads = list(dnaio.open(f))
            assert not f.closed
        assert f.closed

        with FastqReader(filename) as sr:
            tmp_sr = sr
            assert not sr._file.closed
            reads = list(sr)
            assert not sr._file.closed
        assert tmp_sr._file is None


class TestFastaQualReader:
    def test_mismatching_read_names(self):
        fasta = BytesIO(b">name\nACG")
        qual = BytesIO(b">nome\n3 5 7")
        with raises(FormatError):
            list(FastaQualReader(fasta, qual))

    def test_invalid_quality_value(self):
        fasta = BytesIO(b">name\nACG")
        qual = BytesIO(b">name\n3 xx 7")
        with raises(FormatError):
            list(FastaQualReader(fasta, qual))


class TestOpen:
    def setup(self):
        self._tmpdir = mkdtemp()

    def teardown(self):
        shutil.rmtree(self._tmpdir)

    def test_sequence_reader(self):
        # test the autodetection
        with dnaio.open("tests/data/simple.fastq") as f:
            reads = list(f)
        assert reads == simple_fastq

        with dnaio.open("tests/data/simple.fasta") as f:
            reads = list(f)
        assert reads == simple_fasta

        with open("tests/data/simple.fastq") as f:
            reads = list(dnaio.open(f))
        assert reads == simple_fastq

        # make the name attribute unavailable
        f = BytesIO(open("tests/data/simple.fastq", 'rb').read())
        reads = list(dnaio.open(f))
        assert reads == simple_fastq

        f = BytesIO(open("tests/data/simple.fasta", 'rb').read())
        reads = list(dnaio.open(f))
        assert reads == simple_fasta

    def test_autodetect_fasta_format(self):
        path = os.path.join(self._tmpdir, 'tmp.fasta')
        with dnaio.open(path, mode='w') as f:
            assert isinstance(f, FastaWriter)
            for seq in simple_fastq:
                f.write(seq)
        assert list(dnaio.open(path)) == simple_fasta

    def test_write_qualities_to_fasta(self):
        path = os.path.join(self._tmpdir, 'tmp.fasta')
        with dnaio.open(path, mode='w', qualities=True) as f:
            assert isinstance(f, FastaWriter)
            for seq in simple_fastq:
                f.write(seq)
        assert list(dnaio.open(path)) == simple_fasta

    def test_autodetect_fastq_format(self):
        path = os.path.join(self._tmpdir, 'tmp.fastq')
        with dnaio.open(path, mode='w') as f:
            assert isinstance(f, FastqWriter)
            for seq in simple_fastq:
                f.write(seq)
        assert list(dnaio.open(path)) == simple_fastq

    def test_fastq_qualities_missing(self):
        path = os.path.join(self._tmpdir, 'tmp.fastq')
        with raises(ValueError):
            dnaio.open(path, mode='w', qualities=False)


class TestInterleavedReader:
    def test(self):
        expected = [
            (Sequence('read1/1 some text', 'TTATTTGTCTCCAGC', '##HHHHHHHHHHHHH'),
            Sequence('read1/2 other text', 'GCTGGAGACAAATAA', 'HHHHHHHHHHHHHHH')),
            (Sequence('read3/1', 'CCAACTTGATATTAATAACA', 'HHHHHHHHHHHHHHHHHHHH'),
            Sequence('read3/2', 'TGTTATTAATATCAAGTTGG', '#HHHHHHHHHHHHHHHHHHH'))
        ]
        reads = list(InterleavedSequenceReader("tests/data/interleaved.fastq"))
        for (r1, r2), (e1, e2) in zip(reads, expected):
            print(r1, r2, e1, e2)

        assert reads == expected
        with dnaio.open("tests/data/interleaved.fastq", interleaved=True) as f:
            reads = list(f)
        assert reads == expected

    def test_missing_partner(self):
        s = StringIO('@r1\nACG\n+\nHHH')
        with raises(FormatError):
            list(InterleavedSequenceReader(s))

    def test_incorrectly_paired(self):
        s = StringIO('@r1/1\nACG\n+\nHHH\n@wrong_name\nTTT\n+\nHHH')
        with raises(FormatError):
            list(InterleavedSequenceReader(s))


class TestFastaWriter:
    def setup(self):
        self._tmpdir = mkdtemp()
        self.path = os.path.join(self._tmpdir, 'tmp.fasta')

    def teardown(self):
        shutil.rmtree(self._tmpdir)

    def test(self):
        with FastaWriter(self.path) as fw:
            fw.write("name", "CCATA")
            fw.write("name2", "HELLO")
        assert fw._file.closed
        with open(self.path) as t:
            assert t.read() == '>name\nCCATA\n>name2\nHELLO\n'

    def test_linelength(self):
        with FastaWriter(self.path, line_length=3) as fw:
            fw.write("r1", "ACG")
            fw.write("r2", "CCAT")
            fw.write("r3", "TACCAG")
        assert fw._file.closed
        with open(self.path) as t:
            d = t.read()
            assert d == '>r1\nACG\n>r2\nCCA\nT\n>r3\nTAC\nCAG\n'

    def test_write_sequence_object(self):
        with FastaWriter(self.path) as fw:
            fw.write(Sequence("name", "CCATA"))
            fw.write(Sequence("name2", "HELLO"))
        assert fw._file.closed
        with open(self.path) as t:
            assert t.read() == '>name\nCCATA\n>name2\nHELLO\n'

    def test_write_to_file_like_object(self):
        sio = StringIO()
        with FastaWriter(sio) as fw:
            fw.write(Sequence("name", "CCATA"))
            fw.write(Sequence("name2", "HELLO"))
            assert sio.getvalue() == '>name\nCCATA\n>name2\nHELLO\n'
        assert not fw._file.closed

    def test_write_zero_length_sequence(self):
        sio = StringIO()
        with FastaWriter(sio) as fw:
            fw.write(Sequence("name", ""))
            assert sio.getvalue() == '>name\n\n', '{0!r}'.format(sio.getvalue())


class TestFastqWriter:
    def setup(self):
        self._tmpdir = mkdtemp()
        self.path = os.path.join(self._tmpdir, 'tmp.fastq')

    def teardown(self):
        shutil.rmtree(self._tmpdir)

    def test(self):
        with FastqWriter(self.path) as fq:
            fq.writeseq("name", "CCATA", "!#!#!")
            fq.writeseq("name2", "HELLO", "&&&!&&")
        assert fq._file.closed
        with open(self.path) as t:
            assert t.read() == '@name\nCCATA\n+\n!#!#!\n@name2\nHELLO\n+\n&&&!&&\n'

    def test_twoheaders(self):
        with FastqWriter(self.path) as fq:
            fq.write(Sequence("name", "CCATA", "!#!#!", second_header=True))
            fq.write(Sequence("name2", "HELLO", "&&&!&", second_header=True))
        assert fq._file.closed
        with open(self.path) as t:
            assert t.read() == '@name\nCCATA\n+name\n!#!#!\n@name2\nHELLO\n+name2\n&&&!&\n'

    def test_write_to_file_like_object(self):
        sio = StringIO()
        with FastqWriter(sio) as fq:
            fq.writeseq("name", "CCATA", "!#!#!")
            fq.writeseq("name2", "HELLO", "&&&!&&")
        assert sio.getvalue() == '@name\nCCATA\n+\n!#!#!\n@name2\nHELLO\n+\n&&&!&&\n'


class TestInterleavedWriter:
    def test(self):
        reads = [
            (Sequence('A/1 comment', 'TTA', '##H'),
            Sequence('A/2 comment', 'GCT', 'HH#')),
            (Sequence('B/1', 'CC', 'HH'),
            Sequence('B/2', 'TG', '#H'))
        ]
        sio = StringIO()
        with InterleavedSequenceWriter(sio) as writer:
            for read1, read2 in reads:
                writer.write(read1, read2)
        assert sio.getvalue() == '@A/1 comment\nTTA\n+\n##H\n@A/2 comment\nGCT\n+\nHH#\n@B/1\nCC\n+\nHH\n@B/2\nTG\n+\n#H\n'


class TestPairedSequenceReader:
    def test_sequence_names_match(self):
        def match(name1, name2):
            seq1 = Sequence(name1, 'ACGT')
            seq2 = Sequence(name2, 'AACC')
            return sequence_names_match(seq1, seq2)

        assert match('abc', 'abc')
        assert match('abc/1', 'abc/2')
        assert match('abc.1', 'abc.2')
        assert match('abc1', 'abc2')
        assert not match('abc', 'xyz')


def test_head():
    # no line break at end on purpose
    buf = b'first\nsecond\nthird\nfourth\nfifth'
    assert head(buf, 0) == 0
    assert head(buf, 1) == len('first\n')
    assert head(buf, 2) == len('first\n') + len('second\n')
    assert head(buf, 3) == len('first\n') + len('second\n') + len('third\n')
    assert head(buf, 4) == len('first\n') + len('second\n') + len('third\n') + len('fourth\n')
    assert head(buf, 5) == len(buf)
    assert head(buf, 6) == len(buf)
    assert head(buf, 100) == len(buf)


def test_two_fastq_heads():
    buf1 = b'first\nsecond\nthird\nfourth\nfifth'
    buf2 = b'a\nb\nc\nd\ne\nf\ng'
    assert two_fastq_heads(buf1, buf2, len(buf1), len(buf2)) == (
        len(b'first\nsecond\nthird\nfourth\n'), len(b'a\nb\nc\nd\n'))

    assert two_fastq_heads(b'abc', b'def', 3, 3) == (0, 0)
    assert two_fastq_heads(b'abc\n', b'def', 4, 3) == (0, 0)
    assert two_fastq_heads(b'abc', b'def\n', 3, 4) == (0, 0)
    assert two_fastq_heads(b'\n\n\n\n', b'\n\n\n\n', 4, 4) == (4, 4)


def test_fastq_head():
    assert fastq_head(b'') == 0
    assert fastq_head(b'A\n') == 0
    assert fastq_head(b'A\nB') == 0
    assert fastq_head(b'A\nB\n') == 0
    assert fastq_head(b'A\nB\nC') == 0
    assert fastq_head(b'A\nB\nC\n') == 0
    assert fastq_head(b'A\nB\nC\nD') == 0
    assert fastq_head(b'A\nB\nC\nD\n') == 8
    assert fastq_head(b'A\nB\nC\nD\nE') == 8


def test_fastq_record_end():
    assert find_fastq_record_end(b'') == 0
    assert find_fastq_record_end(b'A\n') == 0
    assert find_fastq_record_end(b'A\nB') == 0
    assert find_fastq_record_end(b'A\nB\n') == 0
    assert find_fastq_record_end(b'A\nB\nC') == 0
    assert find_fastq_record_end(b'A\nB\nC\n') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\n') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\n') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\n') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\nG') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\nG\n') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\nG\nH') == 0
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\nG\nH\n') == 16
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\nG\nH\nI') == 16
    assert find_fastq_record_end(b'A\nB\nC\nD\nE\nF\nG\nH\nI\n') == 16


def test_read_paired_chunks():
    with open('tests/data/paired.1.fastq', 'rb') as f1:
        with open('tests/data/paired.2.fastq', 'rb') as f2:
            for c1, c2 in read_paired_chunks(f1, f2, buffer_size=128):
                print(c1, c2)


def test_read_chunks_from_file():
    for data in [b'@r1\nACG\n+\nHHH\n', b'>r1\nACGACGACG\n']:
        assert [m.tobytes() for m in read_chunks_from_file(BytesIO(data))] == [data]

        # Buffer too small
        with raises(OverflowError):
            list(read_chunks_from_file(BytesIO(data), buffer_size=4))
