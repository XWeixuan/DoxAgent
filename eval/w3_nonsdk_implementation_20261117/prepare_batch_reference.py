import ast
import subprocess
from pathlib import Path
source=subprocess.check_output(['git','show','6869269476806207436d7532947ad260c4cfca66:src/doxagent/event_library/repository.py'],text=True,encoding='utf-8')
tree=ast.parse(source)
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='EventLibraryRepository')
method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='published_events')
Path(__file__).with_name('legacy_published_events.py').write_text(ast.get_source_segment(source,method)+'\n',encoding='utf-8')
