"""Typeset docs/specprefill-report.md as a NeurIPS-preprint-style HTML page, for printing to PDF.

    python3 scripts/report_pdf.py docs/specprefill-report.md /tmp/report.html
    microsoft-edge --headless=new --no-pdf-header-footer --print-to-pdf=docs/specprefill-report.pdf file:///tmp/report.html

Needs markdown-it-py; the body font is Nimbus Roman (a Times clone).
"""
import re
import sys
from pathlib import Path

from markdown_it import MarkdownIt

src, out = Path(sys.argv[1]), Path(sys.argv[2])
text = src.read_text()

title = re.search(r'^# (.+)$', text, re.M).group(1)
note = re.search(r'^\*(Technical report draft.*?)\*$', text, re.M | re.S).group(1)
author = re.search(r'^\*\*(.+?)\*\*$', text, re.M).group(1)
abstract = re.search(r'^## Abstract\n(.*?)(?=^## )', text, re.M | re.S).group(1)
body = text[re.search(r'^## 1 ', text, re.M).start():]
note = ' '.join(note.split()).replace(' Authors: [to be added].', '')
note = re.sub(r'`([^`]+)`', r'<code>\1</code>', note)

md = MarkdownIt('commonmark').enable('table')
abstract_html = md.render(abstract)
body_html = md.render(body)

# Number the tables (Table 1, 2, ...) with their captions, in order.
count = 0
CAPTIONS = [
    'Median Flash-Next prefill time on the P100 by fresh tokens read in one call.',
    'Student-only replay of the 122-turn session under sparse-prefill settings. Gold: answer line delivered '
    'intact; Jaccard: selection overlap with dense prefill.',
    'Held-out synthetic agent sessions (30 scored fields each; seeds never tuned on). End-to-end wall time.',
    'The real ~253K-token session by depth bin: probes correct and end-to-end wall time. Native and SpecPrefill '
    'use FP16 K/V; the compaction arm is an earlier run on the same case.',
    'Probe classes on the real session (same runs as Table 4).',
    'Ablation history on the real session. The first two speed-ups are against the int8-K/V native baseline.',
    'Replayed-edit evaluation: blind side-by-side grades (correct + half of partial) by a separate model.',
    'Deep live coding-agent evaluation (11 tasks per arm): seconds per output token by conversation depth, '
    'tasks passed and mean seconds per model call.',
]


def caption(match):
    global count
    count += 1
    return (f'<div class="tab"><div class="cap"><b>Table {count}.</b> {CAPTIONS[count - 1]}</div><table>')


body_html = re.sub(r'<table>', caption, body_html)
body_html = body_html.replace('</table>', '</table></div>')
# Section headings: "1 Introduction" -> numbered heading; "3.1 Roles" -> subsection.
body_html = re.sub(r'<h2>(\d+) (.+?)</h2>', r'<h2><span class="num">\1</span>\2</h2>', body_html)
body_html = re.sub(r'<h3>(\d+\.\d+) (.+?)</h3>', r'<h3><span class="num">\1</span>\2</h3>', body_html)
body_html = body_html.replace('<h2>Reproducibility</h2>', '<h2 class="unnum">Reproducibility</h2>')
body_html = body_html.replace('<h2>AI contribution statement</h2>', '<h2 class="unnum">AI contribution statement</h2>')
body_html = body_html.replace('<h2>References</h2>', '<h2 class="unnum refs-h">References</h2>')
# References: the list after the References heading becomes a hanging-indent list.
body_html = re.sub(r'(<h2 class="unnum refs-h">References</h2>\s*<p>.*?</p>\s*)<ul>',
                   r'\1<ul class="refs">', body_html, flags=re.S)

html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>
@page {{
  size: letter;
  margin: 1in 1in 1in 1in;
  @bottom-center {{ content: counter(page); font: 9pt "Nimbus Roman", "Liberation Serif", serif; }}
}}
html {{ background: #fff; }}
body {{
  font-family: "Nimbus Roman", "Liberation Serif", "Times New Roman", serif;
  font-size: 10pt; line-height: 1.32; color: #000; margin: 0;
  text-align: justify; hyphens: auto; -webkit-hyphens: auto;
}}
.title-block {{ text-align: center; margin: 0 0 18pt; }}
.rule-top {{ border-top: 4pt solid #000; margin-bottom: 12pt; }}
.rule-bot {{ border-top: 1pt solid #000; margin-top: 12pt; }}
.title {{ hyphens: none; -webkit-hyphens: none; font-size: 17pt; font-weight: bold; line-height: 1.2; margin: 0 0.3in; text-align: center; }}
.authors {{ margin-top: 18pt; font-size: 10pt; text-align: center; }}
.authors b {{ font-weight: bold; }}
.affil {{ font-size: 9.5pt; margin-top: 2pt; }}
.note {{ font-size: 8.5pt; font-style: italic; margin: 10pt 0.6in 0; text-align: center; }}
.abstract {{ margin: 0 0.5in 14pt; }}
.abstract h2 {{ text-align: center; font-size: 12pt; margin: 0 0 6pt; }}
.abstract p {{ margin: 0 0 5pt; }}
h2 {{ font-size: 12pt; font-weight: bold; margin: 14pt 0 6pt; break-after: avoid; text-align: left; }}
h3 {{ font-size: 10pt; font-weight: bold; margin: 10pt 0 4pt; break-after: avoid; text-align: left; }}
h2 .num, h3 .num {{ display: inline-block; min-width: 1.6em; }}
h3 .num {{ min-width: 2.2em; }}
p {{ margin: 0 0 6pt; }}
ul, ol {{ margin: 0 0 6pt; padding-left: 1.6em; }}
li {{ margin: 0 0 2pt; }}
li > p {{ margin: 0; }}
code {{ font-family: "DejaVu Sans Mono", monospace; font-size: 8.3pt; }}
strong {{ font-weight: bold; }}
.tab {{ margin: 8pt 0 10pt; break-inside: avoid; }}
.cap {{ font-size: 9pt; margin-bottom: 3pt; text-align: left; }}
table {{ border-collapse: collapse; margin: 0 auto; font-size: 8.8pt; line-height: 1.25;
         border-top: 1.2pt solid #000; border-bottom: 1.2pt solid #000; }}
thead th {{ border-bottom: 0.6pt solid #000; font-weight: bold; }}
th, td {{ padding: 2.2pt 6pt; vertical-align: top; text-align: left; }}
td[style*="right"], th[style*="right"] {{ font-variant-numeric: tabular-nums; }}
.refs {{ list-style: none; padding-left: 0; }}
.refs li {{ padding-left: 1.4em; text-indent: -1.4em; font-size: 9pt; margin-bottom: 3pt; text-align: left; }}
.refs-h + p {{ font-size: 8.5pt; font-style: italic; }}
</style></head>
<body>
<div class="title-block">
  <div class="rule-top"></div>
  <div class="title">{title}</div>
  <div class="rule-bot"></div>
  <div class="authors"><b>{author}</b></div>
  <div class="note">{note}</div>
</div>
<div class="abstract"><h2>Abstract</h2>{abstract_html}</div>
{body_html}
</body></html>
"""
out.write_text(html)
print(f'{out}: {count} tables')
