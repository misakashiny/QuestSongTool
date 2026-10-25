"""Pure comparison report rendering; callers own snapshots, labels and file I/O."""
from collections import Counter
import csv
import io
from typing import Mapping, Sequence


def render_report(rows: Sequence[Sequence[str]], fmt: str, *, labels: Mapping[str, str],
                  generated_at: str = '', local_root: str | None = None,
                  quest_root: str | None = None) -> str:
    header = [labels['report.column.'+key] for key in
              ('status','folder','title','author','bpm','duration','difficulties','rating')]
    if fmt == 'csv':
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        writer.writerows(rows)
        return buffer.getvalue()
    # Preserve the existing caller's non-CSV Markdown behavior.
    counts = Counter(row[0] for row in rows)
    lines = [
        '# '+labels['report.document_title'], '',
        labels['report.generated_line'].format(value=generated_at),
        labels['report.local_line'].format(value=local_root if local_root is not None else labels['report.unset']),
        labels['report.quest_line'].format(value=quest_root if quest_root is not None else labels['report.unset']),
        labels['report.summary_line'].format(total=len(rows),
            both=counts.get(labels['report.state.both'],0),
            local=counts.get(labels['report.state.local'],0),
            quest=counts.get(labels['report.state.quest'],0)), '',
        '| '+' | '.join(header)+' |', '|'+ '---|'*len(header),
    ]
    for row in rows:
        lines.append('| '+' | '.join(cell.replace('|','\\|') for cell in row)+' |')
    lines.append('')
    return '\n'.join(lines)
