The file loglines.txt contains 20 log lines from a Linux server, one per line.
Classify EACH line (numbered 1-20, in file order) as exactly one of:
  ecc | oom | network | benign

- ecc: a memory ECC error event
- oom: part of an out-of-memory kill sequence
- network: link/address/interface state changes
- benign: routine service activity, nothing actionable

Write out.csv in the working directory with one row per line:
<line_number>,<label>
No header, no prose, nothing else.
