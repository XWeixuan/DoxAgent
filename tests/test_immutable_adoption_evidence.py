import os
import time

from doxagent.codex_runtime.immutable_evidence import adopt_file,collect_unused


def test_shared_adoption_bytes_preserve_all_paths_and_are_independent_of_mutable_source(tmp_path):
    source=tmp_path/'source.txt';source.write_text('source evidence')
    first=tmp_path/'adopt1'/'file.txt';second=tmp_path/'adopt2'/'file.txt';cache=tmp_path/'contents'
    adopt_file(source,first,cache);adopt_file(source,second,cache)
    assert first.stat().st_ino==second.stat().st_ino
    assert first.stat().st_ino!=source.stat().st_ino
    source.write_text('changed mutable source')
    assert first.read_text()==second.read_text()=='source evidence'
    old=time.time()-8*86400
    for path in cache.iterdir():os.utime(path,(old,old))
    assert collect_unused(cache)==0
    first.unlink();second.unlink()
    assert collect_unused(cache)==1


def test_existing_immutable_copies_relink_without_changing_their_content(tmp_path):
    first=tmp_path/'first';second=tmp_path/'second';first.write_bytes(b'exact');second.write_bytes(b'exact')
    cache=tmp_path/'contents';adopt_file(first,first,cache);receipt=adopt_file(second,second,cache)
    assert first.read_bytes()==second.read_bytes()==b'exact'
    assert first.stat().st_ino==second.stat().st_ino
    assert receipt['sha256']
