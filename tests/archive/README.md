# archive —— 一次性脚本与取证脚本（保留备查，不参与日常使用）

这些脚本产生于重构过程中的"专项排查"，结论已经写进 `docs/` 的文档里，
所以平时不需要运行它们；保留在这里是为了在有人质疑某个结论时能**原样复现证据**。

| 脚本 | 当时用来回答的问题 | 结论写在哪 |
|---|---|---|
| `probe_copyhere.ps1` | `Folder.CopyHere` 各标志位在"目标已存在"时到底覆盖还是跳过？会不会弹窗？ | `docs/06_Quest3连接与文件夹选择.md` 第五节 |
| `probe_delete.ps1` | 删除 shell/MTP 条目要多久？会不会弹确认框？ | 同上（所以脚本不自动删备份） |
| `probe_thread_after.py` | 子线程里调 `root.after` 在 `wait_window` 驱动下会不会炸？ | `docs/02_UI设计说明.md` |
| `probe_ctrlc.py` / `probe_list_light.py` | "Ctrl+C 失效""标题栏黑块"是控件的问题还是宿主环境的问题？ | `docs/02_UI设计说明.md` |
| `diag_layout*.py` | 窗口被撑到 1885px、表格压住翻页条 269px —— 到底是哪个控件？ | 同上 |
| `diag_blackbox.py` / `diag_artifacts.py` | 深色残影是哪个控件的哪一层画出来的？ | 同上 |
| `cost_report.py` | 当天 token 花在哪（按小时/会话拆账） | 移交说明 `HANDOVER.md` |
| `verify_*.py` / `static_analysis.py` | 子代理独立取证：原版复制删源、高亮错位、BOM/GBK 失败等 | `docs/01_代码审查-原版问题清单.md` |

> **为什么留着而不删**：这些脚本回答了「当时为什么会得出这个结论」。
> 文档被人质疑时，可以拿它们**原样复现证据**；删了就只剩一句没有证据的结论。
> 合计 23 个文件，是 131 个被跟踪文件里的 23 个。

运行方式（部分脚本写死了旧路径 `_review/`，需要先把 `REVIEW` 常量改成 `tests/`）：
```powershell
python tests/archive/diag_layout2.py      # 打印各区域实际几何
python tests/archive/probe_ctrlc.py       # 单独验证 Ctrl+C 绑定
```
