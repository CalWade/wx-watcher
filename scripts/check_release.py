#!/usr/bin/env python3
"""Review source/zip contents without printing possible secret values."""
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ['README.md','README.en.md','LICENSE','THIRD_PARTY.md','SECURITY.md','CONTRIBUTING.md',
    'docs/INSTALL.md','docs/KEYTOOL.md','docs/DSH.md','docs/FEISHU.md','docs/OPERATIONS.md',
    'docs/API.md','docs/DEVELOPMENT.md','native/Vendor/LICENSE.greenbubbles']
PRIVATE = {'.runtime','work','output','.venv','.env','login.keychain-db','master-key.txt','local.toml','reply.local.toml'}
PATTERNS = [re.compile(r'/Users/(?!example\b|user\b)[A-Za-z][A-Za-z0-9_.-]*/'),
    re.compile(r'wxid_[a-z0-9]{12,}'),
    re.compile(r'''(?i)(?:api_key|app_secret|master_key|passphrase)\s*[=:]\s*["'][A-Za-z0-9+/=_-]{24,}["']''')]

def main():
    errors=[]
    for name in REQUIRED:
        if not (ROOT/name).is_file(): errors.append('missing required file: '+name)
    result=subprocess.run(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,capture_output=True,text=True,check=True)
    files=sorted(set(result.stdout.splitlines()))
    exact=[]
    local=ROOT/'.runtime/master-key.txt'
    if local.exists():
        value=local.read_text().strip()
        if re.fullmatch(r'[0-9a-fA-F]{64}',value): exact.append(value.lower())
    for name in files:
        path=ROOT/name
        if not path.is_file(): continue
        if any(p in PRIVATE for p in Path(name).parts): errors.append('private file tracked: '+name)
        try: text=path.read_text()
        except (UnicodeError,OSError): continue
        for pattern in PATTERNS:
            if pattern.search(text): errors.append('possible private material: '+name);break
        if any(secret in text.lower() for secret in exact): errors.append('local credential found: '+name)
        if path.suffix=='.md':
            for link in re.findall(r'\]\(([^)]+)\)',text):
                if link.startswith(('http:','https:','#','mailto:')):continue
                target=link.split('#')[0].split(' ')[0]
                if target and not (path.parent/target).exists(): errors.append('broken document link: '+name+' -> '+target)
    for arg in sys.argv[1:]:
        with zipfile.ZipFile(arg) as archive:
            for name in archive.namelist():
                if any(p in PRIVATE for p in Path(name).parts):errors.append('private archive member: '+name)
    if errors:
        for item in sorted(set(errors)):print(item)
        return 1
    print(f'Release source checks passed: {len(files)} files; required docs, local privacy patterns and credential exclusion.')
    return 0

if __name__=='__main__':raise SystemExit(main())
