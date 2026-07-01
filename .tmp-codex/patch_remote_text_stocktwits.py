from __future__ import annotations

from pathlib import Path

root = Path('/root/doxagent')

def write(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')

env_example = root / '.env.example'
text = env_example.read_text(encoding='utf-8')
if 'STOCKTWITS_RAPIDAPI_FALLBACK_KEY=' not in text:
    text = text.replace(
        'STOCKTWITS_RAPIDAPI_KEY=\n',
        'STOCKTWITS_RAPIDAPI_KEY=\nSTOCKTWITS_RAPIDAPI_FALLBACK_KEY=\n',
        1,
    )
    write(env_example, text)

changelog = root / 'changelog'
line = '- Added a Stocktwits RapidAPI fallback key setting and collector retry guard so fallback credentials are used only after the primary key fails through the configured retry window.'
text = changelog.read_text(encoding='utf-8')
if line not in text:
    text = text.replace('## 2026-06-28\n\n', '## 2026-06-28\n\n' + line + '\n', 1)
    write(changelog, text)
print('remote_text_patch_done')
