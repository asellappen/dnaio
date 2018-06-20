# kate: syntax Python;
# cython: profile=False, emit_code_comments=False
from __future__ import print_function, division, absolute_import
# TODO remove the __future__ imports and set language_level=3
from . import _shorten, FormatError, BinaryFileReader

from libc.string cimport strncmp
cimport cython

# It would be nice to be able to have the first parameter be a
# unsigned char[:] (memory view), but this fails with a BufferError
# when a bytes object is passed in.
# See <https://stackoverflow.com/questions/28203670/>

ctypedef fused bytes_or_bytearray:
	bytes
	bytearray


#@cython.boundscheck(False)
def head(bytes_or_bytearray buf, Py_ssize_t lines):
	"""
	Skip forward by a number of lines in the given buffer and return
	how many bytes this corresponds to.
	"""
	cdef:
		Py_ssize_t pos = 0
		Py_ssize_t linebreaks_seen = 0
		Py_ssize_t length = len(buf)
		unsigned char* data = buf

	while linebreaks_seen < lines and pos < length:
		if data[pos] == '\n':
			linebreaks_seen += 1
		pos += 1
	return pos


def fastq_head(bytes_or_bytearray buf, Py_ssize_t end=-1):
	"""
	Return an integer length such that buf[:length] contains the highest
	possible number of complete four-line FASTQ records.

	If end is -1, the full buffer is searched. Otherwise only buf[:end].
	"""
	cdef:
		Py_ssize_t pos = 0
		Py_ssize_t linebreaks = 0
		Py_ssize_t length = len(buf)
		unsigned char* data = buf
		Py_ssize_t record_start = 0

	if end != -1:
		length = min(length, end)
	while True:
		while pos < length and data[pos] != '\n':
			pos += 1
		if pos == length:
			break
		pos += 1
		linebreaks += 1
		if linebreaks == 4:
			linebreaks = 0
			record_start = pos

	# Reached the end of the data block
	return record_start


def two_fastq_heads(bytes_or_bytearray buf1, bytes_or_bytearray buf2, Py_ssize_t end1, Py_ssize_t end2):
	"""
	Skip forward in the two buffers by multiples of four lines.

	Return a tuple (length1, length2) such that buf1[:length1] and
	buf2[:length2] contain the same number of lines (where the
	line number is divisible by four).
	"""
	cdef:
		Py_ssize_t pos1 = 0, pos2 = 0
		Py_ssize_t linebreaks = 0
		unsigned char* data1 = buf1
		unsigned char* data2 = buf2
		Py_ssize_t record_start1 = 0
		Py_ssize_t record_start2 = 0

	while True:
		while pos1 < end1 and data1[pos1] != '\n':
			pos1 += 1
		if pos1 == end1:
			break
		pos1 += 1
		while pos2 < end2 and data2[pos2] != '\n':
			pos2 += 1
		if pos2 == end2:
			break
		pos2 += 1
		linebreaks += 1
		if linebreaks == 4:
			linebreaks = 0
			record_start1 = pos1
			record_start2 = pos2

	# Hit the end of the data block
	return record_start1, record_start2


cdef class Sequence(object):
	"""
	A record in a FASTA or FASTQ file. For FASTA, the qualities attribute
	is None. For FASTQ, qualities is a string and it contains the qualities
	encoded as ascii(qual+33).
	"""
	cdef:
		public str name
		public str sequence
		public str qualities

	def __cinit__(self, str name, str sequence, str qualities=None):
		"""Set qualities to None if there are no quality values"""
		self.name = name
		self.sequence = sequence
		self.qualities = qualities

		if qualities is not None and len(qualities) != len(sequence):
			rname = _shorten(name)
			raise FormatError("In read named {0!r}: length of quality sequence ({1}) and length "
				"of read ({2}) do not match".format(
					rname, len(qualities), len(sequence)))
	
	def __getitem__(self, key):
		"""slicing"""
		return self.__class__(
			self.name,
			self.sequence[key],
			self.qualities[key] if self.qualities is not None else None)

	def __repr__(self):
		qstr = ''
		if self.qualities is not None:
			qstr = ', qualities={0!r}'.format(_shorten(self.qualities))
		return '<Sequence(name={0!r}, sequence={1!r}{2})>'.format(_shorten(self.name), _shorten(self.sequence), qstr)

	def __len__(self):
		return len(self.sequence)

	def __richcmp__(self, other, int op):
		if 2 <= op <= 3:
			eq = self.name == other.name and \
				self.sequence == other.sequence and \
				self.qualities == other.qualities
			if op == 2:
				return eq
			else:
				return not eq
		else:
			raise NotImplementedError()

	def __reduce__(self):
		return (Sequence, (self.name, self.sequence, self.qualities))


class FastqReader(BinaryFileReader):
	"""
	Reader for FASTQ files. Does not support multi-line FASTQ files.
	"""
	def __init__(self, file, sequence_class=Sequence):
		"""
		file is a filename or a file-like object.
		If file is a filename, then .gz files are supported.
		"""
		super(FastqReader, self).__init__(file)
		self.sequence_class = sequence_class
		self.delivers_qualities = True

	def __iter__(self):
		"""
		Parse the FASTQ file and yield Sequence objects
		"""
		cdef:
			bytearray buf = bytearray(1048576)
			char[:] buf_view = buf
			char* c_buf
			int endskip
			str name
			char* name_encoded
			Py_ssize_t bufstart, bufend, pos, record_start, sequence_start
			Py_ssize_t second_header_start, sequence_length, qualities_start
			Py_ssize_t second_header_length, name_length
			Py_ssize_t line
			bint custom_class = self.sequence_class is not Sequence

		# buf is a byte buffer that is re-used in each iteration. Its layout is:
		#
		# |-- complete records --|
		# +---+------------------+---------+-------+
		# |   |                  |         |       |
		# +---+------------------+---------+-------+
		# ^   ^                  ^         ^       ^
		# 0   bufstart           end       bufend  len(buf)
		#
		# buf[0:start] is the 'leftover' data that could not be processed
		# in the previous iteration because it contained an incomplete
		# FASTQ record.

		readinto = self._file.readinto
		bufstart = 0
		line = 1
		c_buf = buf

		# The input file is processed in chunks that each fit into buf
		while True:
			bufend = readinto(buf_view[bufstart:]) + bufstart
			if bufstart == bufend:
				# End of file
				break

			# Parse all complete FASTQ records in this chunk
			pos = 0
			record_start = 0
			while True:
				# Parse the name
				if c_buf[pos] != '@':
					raise FormatError("Line {} in FASTQ file is expected to "
						"start with '@', but found {!r}".format(line, chr(c_buf[pos])))
				pos += 1
				while pos < bufend and c_buf[pos] != '\n':
					pos += 1
				if pos == bufend:
					break
				endskip = 1 if c_buf[pos-1] == '\r' else 0
				name_length = pos - endskip - record_start - 1
				name_encoded = c_buf + record_start + 1
				name = c_buf[record_start+1:pos-endskip].decode('ascii')

				pos += 1
				line += 1

				# Parse the sequence
				sequence_start = pos
				while pos < bufend and c_buf[pos] != '\n':
					pos += 1
				if pos == bufend:
					break
				endskip = 1 if c_buf[pos-1] == '\r' else 0
				sequence = c_buf[sequence_start:pos-endskip].decode('ascii')
				sequence_length = pos - endskip - sequence_start
				pos += 1
				line += 1

				# Parse second header
				second_header_start = pos
				if pos == bufend:
					break
				if c_buf[pos] != '+':
					raise FormatError("Line {} in FASTQ file is expected to "
						"start with '+', but found {!r}".format(line, chr(c_buf[pos])))
				pos += 1
				while pos < bufend and c_buf[pos] != '\n':
					pos += 1
				if pos == bufend:
					break
				line += 1
				endskip = 1 if c_buf[pos-1] == '\r' else 0
				second_header_length = pos - endskip - second_header_start - 1
				if second_header_length == 0:
					second_header = False
				else:
					if (name_length != second_header_length or
							strncmp(c_buf+second_header_start+1,
								name_encoded, second_header_length) != 0):
						raise FormatError(
							"At line {}: Sequence descriptions in the "
							"FASTQ file don't match ('{}' != '{}').\n"
							"The second sequence description must be either "
							"empty or equal to the first description.".format(
								line, name_encoded.decode('ascii'),
								c_buf[second_header_start+1:pos-endskip]
								.decode('ascii')))
					second_header = True
				pos += 1
				line += 1

				# Parse qualities
				qualities_start = pos
				while pos < bufend and c_buf[pos] != '\n':
					pos += 1
				if pos == bufend:
					break
				endskip = 1 if c_buf[pos-1] == '\r' else 0
				qualities = c_buf[qualities_start:pos-endskip].decode('ascii')
				if pos - endskip - qualities_start != sequence_length:
					raise FormatError("At line {}: Length of sequence and "
						"qualities differ.".format(line))
				pos += 1
				line += 1
				if custom_class:
					yield self.sequence_class(name, sequence, qualities)
				else:
					yield Sequence.__new__(Sequence, name, sequence, qualities)
				record_start = pos
				if pos == bufend:
					break
			if pos == bufend:
				bufstart = bufend - record_start
				buf[0:bufstart] = buf[record_start:bufend]
		if pos > record_start:
			raise FormatError('FASTQ file ended prematurely at line {}. '
				'The incomplete final record was: '
				'{!r}'.format(line, _shorten(buf[record_start:pos].decode(), 500)))


class FastqReaderOld(BinaryFileReader):
	"""
	Reader for FASTQ files. Does not support multi-line FASTQ files.
	"""
	def __init__(self, file, sequence_class=Sequence):
		"""
		file is a filename or a file-like object.
		If file is a filename, then .gz files are supported.
		"""
		super(FastqReaderOld, self).__init__(file)
		self.sequence_class = sequence_class
		self.delivers_qualities = True

	def __iter__(self):
		"""
		Yield Sequence objects
		"""
		cdef int i = 0
		cdef int strip
		cdef str line, name, qualities, sequence, name2
		sequence_class = self.sequence_class

		it = iter(self._file)
		line = next(it)
		if not (line and line[0] == '@'):
			raise FormatError("Line {0} in FASTQ file is expected to start with '@', but found {1!r}".format(i+1, line[:10]))
		strip = -2 if line.endswith('\r\n') else -1
		name = line[1:strip]

		i = 1
		for line in it:
			if i == 0:
				if not (line and line[0] == '@'):
					raise FormatError("Line {0} in FASTQ file is expected to start with '@', but found {1!r}".format(i+1, line[:10]))
				name = line[1:strip]
			elif i == 1:
				sequence = line[:strip]
			elif i == 2:
				if line == '+\n':  # check most common case first
					name2 = ''
				else:
					line = line[:strip]
					if not (line and line[0] == '+'):
						raise FormatError("Line {0} in FASTQ file is expected to start with '+', but found {1!r}".format(i+1, line[:10]))
					if len(line) > 1:
						if not line[1:] == name:
							raise FormatError(
								"At line {0}: Sequence descriptions in the FASTQ file don't match "
								"({1!r} != {2!r}).\n"
								"The second sequence description must be either empty "
								"or equal to the first description.".format(i+1,
									name, line[1:]))
						second_header = True
					else:
						second_header = False
			elif i == 3:
				if len(line) == len(sequence) - strip:
					qualities = line[:strip]
				else:
					qualities = line.rstrip('\r\n')
				yield sequence_class(name, sequence, qualities, second_header=second_header)
			i = (i + 1) % 4
		if i != 0:
			raise FormatError("FASTQ file ended prematurely")
