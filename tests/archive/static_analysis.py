# -*- coding: utf-8 -*-
"""Static analysis: function lengths, magic numbers, formatting styles, thread/Tk call sites."""
import ast, sys, collections

PATH = r"D:\QA\Quest3bs\源代码_原始版.py"
src = open(PATH, encoding="utf-8").read()
tree = ast.parse(src)
lines = src.splitlines()

print("=== functions/methods longer than 80 lines ===")
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        n = node.end_lineno - node.lineno + 1
        if n > 80:
            print(f"  {n:4d} lines  {node.name}()  @ line {node.lineno}-{node.end_lineno}")

print("\n=== top 12 longest functions ===")
fns = sorted(((nd.end_lineno - nd.lineno + 1, nd.name, nd.lineno, nd.end_lineno)
              for nd in ast.walk(tree) if isinstance(nd, ast.FunctionDef)), reverse=True)[:12]
for n, name, a, b in fns:
    print(f"  {n:4d}  {name}()  {a}-{b}")

print("\n=== bare `except:` ===")
for node in ast.walk(tree):
    if isinstance(node, ast.ExceptHandler) and node.type is None:
        print(f"  line {node.lineno}: {lines[node.lineno-1].strip()}")

print("\n=== `except ...: pass` / `continue` (silent swallow) ===")
for node in ast.walk(tree):
    if isinstance(node, ast.ExceptHandler):
        body = node.body
        if len(body) == 1 and isinstance(body[0], (ast.Pass, ast.Continue)):
            kw = "pass" if isinstance(body[0], ast.Pass) else "continue"
            print(f"  line {node.lineno}: {lines[node.lineno-1].strip()} -> {kw}")

print("\n=== string formatting style ===")
print("  str.format(...) calls :", src.count(".format("))
print("  % formatting (approx):", sum(1 for l in lines if "%s" in l or "%d" in l))
print("  f-strings (approx)   :", sum(1 for l in lines if 'f"' in l or "f'" in l))

print("\n=== datetime.now() (naive local time stored in DB) ===")
for i, l in enumerate(lines, 1):
    if "datetime.now()" in l:
        print(f"  line {i}: {l.strip()}")

print("\n=== type hints present? ===")
hints = [nd.name for nd in ast.walk(tree) if isinstance(nd, ast.FunctionDef)
         and (nd.returns is not None or any(a.annotation for a in nd.args.args))]
print("  annotated functions:", hints or "NONE (0 annotated out of %d functions)" %
      sum(1 for nd in ast.walk(tree) if isinstance(nd, ast.FunctionDef)))

print("\n=== inline hex colour literals ===")
cols = collections.Counter()
for i, l in enumerate(lines, 1):
    if "#" in l and any(c in l for c in ('fg_color', 'hover_color', 'text_color')):
        for tok in l.replace('"', " ").replace("'", " ").split():
            if tok.startswith("#") and len(tok) in (4, 7):
                cols[tok] += 1
print("  distinct colours:", len(cols), "| total occurrences:", sum(cols.values()))
print("  ", dict(cols.most_common(8)))

print("\n=== calls that touch Tk/Tcl from non-main threads (candidates) ===")
for i, l in enumerate(lines, 1):
    s = l.strip()
    if s.startswith("self.root.after(") or ".winfo_" in s or "messagebox" in s and "def " not in s:
        pass
for i, l in enumerate(lines, 1):
    if "self.root.after(" in l:
        print(f"  line {i}: {l.strip()[:100]}")
