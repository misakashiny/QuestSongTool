"""Report format contracts without GUI, filesystem, database or device access."""
import copy
import csv
import io
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def forbidden(*args,**kwargs):raise AssertionError('report renderer attempted a side effect')


def main():
    with patch('subprocess.Popen',forbidden),patch('threading.Thread.start',forbidden):
        from quest_tool.reporting import render_report
        assert not any(name in sys.modules for name in ('quest_tool.adb','quest_tool.database','quest_tool.storage'))
        from quest_tool.messages import catalog
        labels=catalog('zh')
        rows=(('两边都有','a|b','曲目, "A"','作者','120','3:00','Expert/Expert+','5'),
              ('仅本地','本地','line\nnext','','','未知','',''),
              ('仅设备','设备','Remote','Artist','90','1:00','Hard',''))
        original=copy.deepcopy(rows)
        text=render_report(rows,'csv',labels=labels)
        parsed=list(csv.reader(io.StringIO(text,newline='')))
        assert parsed[0]==['状态','文件夹','标题','作者','BPM','时长','难度','评分']
        assert parsed[1:]==[list(row) for row in rows]
        assert '"曲目, ""A"""' in text and '"line\nnext"' in text
        assert text.endswith('\r\n') and '\r\r\n' not in text
        data=io.BytesIO()
        wrapper=io.TextIOWrapper(data,encoding='utf-8-sig',newline='')
        wrapper.write(text);wrapper.flush()
        assert data.getvalue().startswith(b'\xef\xbb\xbf') and b'\r\r\n' not in data.getvalue()
        mdrows=(rows[0],('仅本地','本地','Local','','','未知','',''),rows[2])
        md=render_report(mdrows,'md',labels=labels,generated_at='2026-10-22 12:34:56',
                         local_root='D:/曲库',quest_root='/storage/emulated/0/Songs')
        assert md.startswith('# Quest 歌单对比报告\n\n- 生成时间：2026-10-22 12:34:56\n')
        assert '- 本地路径：`D:/曲库`' in md and '- Quest 路径：`/storage/emulated/0/Songs`' in md
        assert '- 合计 3 首：两边都有 1、仅本地 1、仅设备 1' in md
        assert '| 状态 | 文件夹 | 标题 | 作者 | BPM | 时长 | 难度 | 评分 |' in md
        assert '| 两边都有 | a\\|b |' in md and md.endswith('\n')
        empty=render_report((),'md',labels=labels,generated_at='fixed')
        assert '- 合计 0 首：两边都有 0、仅本地 0、仅设备 0' in empty and empty.count('（未设置）')==2
        assert render_report((),'csv',labels=labels)=='状态,文件夹,标题,作者,BPM,时长,难度,评分\r\n'
        assert '- 本地路径：``' in render_report((),'md',labels=labels,local_root='')
        assert render_report((),'legacy_non_csv',labels=labels)==render_report((),'md',labels=labels)
        english=catalog('en')
        translated=tuple((english['report.state.'+key],*row[1:]) for key,row in zip(('both','local','quest'),mdrows))
        emd=render_report(translated,'md',labels=english,generated_at='fixed')
        assert '- Total 3: on both 1, local only 1, Quest only 1' in emd
        assert '| Status | Folder | Title | Artist | BPM | Duration | Difficulties | Rating |' in emd
        assert rows==original
    assert not any(name.startswith(('tkinter','customtkinter','PySide6')) for name in sys.modules)
    assert not any(name in sys.modules for name in ('quest_tool.adb','quest_tool.database'))
    print('PASS pure bilingual report renderer: 8 columns, CSV quoting/BOM/newline, Markdown paths/counts/pipe, empty values, no GUI/I/O')


if __name__=='__main__':main()
