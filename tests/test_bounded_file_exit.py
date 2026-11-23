import os
from pathlib import Path
from doxagent.v2_read.detached_files import drain,unlink_page


def test_large_detached_tree_advances_in_entry_pages_and_keeps_shared_blocks(tmp_path):
    root=tmp_path/'trash'/('a'*64+'.'+'b'*32);root.mkdir(parents=True)
    for n in range(40):(root/str(n)).write_text('retired input')
    shared=tmp_path/'canonical';shared.write_text('business evidence')
    os.link(shared,root/'alias')
    assert drain(root.parent,limit=1,max_entries=4,seconds=5)==0
    assert root.is_dir() and len(list(root.iterdir()))<41
    for _ in range(20):drain(root.parent,limit=1,max_entries=4,seconds=5)
    assert not root.exists() and shared.read_text()=='business evidence'


def test_empty_directory_work_is_bounded_too(tmp_path):
    root=tmp_path/'retired';root.mkdir()
    for n in range(30):(root/str(n)).mkdir()
    complete,entries,_=unlink_page(root,max_entries=3,deadline=float('inf'))
    assert not complete and entries==3 and root.is_dir()
