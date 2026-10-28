"""Rebuild the Qt one-file executable using the existing build environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--adb-source',type=Path,default=Path(r'C:\Android\Sdk\platform-tools'))
    parser.add_argument('--output',type=Path,default=ROOT/'dist')
    parser.add_argument('--work',type=Path,default=ROOT/'build'/'pyinstaller')
    args=parser.parse_args()
    env=dict(os.environ,QUEST_ADB_SOURCE=str(args.adb_source.resolve()))
    # Do not collect unrelated DLLs (for example Poppler's versioned ICU) from
    # the host's broad PATH. Qt discovers its own package directory via hooks.
    system=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    env['PATH']=os.pathsep.join(map(str,(Path(sys.executable).parent,Path(sys.executable).parent/'DLLs',system/'System32',system)))
    subprocess.run([sys.executable,'-B','-m','PyInstaller','--noconfirm','--distpath',str(args.output.resolve()),
                    '--workpath',str(args.work.resolve()),str(ROOT/'packaging'/'quest_song_tool.spec')],cwd=ROOT,env=env,check=True)
    exe=args.output/'QuestSongTool.exe'
    manifest=dict(file=exe.name,size=exe.stat().st_size,sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    (args.output/'build-manifest.json').write_text(json.dumps(manifest,indent=2),'utf-8')
    print(json.dumps(manifest))


if __name__=='__main__':main()
