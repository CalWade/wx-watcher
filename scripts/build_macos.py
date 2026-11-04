#!/usr/bin/env python3
"""Build self-contained Mac apps; never copies private runtime/configuration."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import plistlib
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'build/macos'
DIST=ROOT/'dist'
VERSION='0.2.0'

def run(args,**kwargs):subprocess.run([str(x) for x in args],cwd=ROOT,check=True,**kwargs)

def main():
    if sys.platform!='darwin':raise SystemExit('macOS is required')
    sql=shutil.which('sqlcipher')
    if not sql:raise SystemExit('Install SQLCipher before building')
    BUILD.mkdir(parents=True,exist_ok=True);DIST.mkdir(exist_ok=True)
    run(['swift','build','--package-path','native','-c','release'])
    run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','wx-watcher',
        '--distpath',BUILD/'python-dist','--workpath',BUILD/'pyinstaller-work','--specpath',BUILD,
        '--paths',ROOT,'--collect-submodules','wx_watcher','--collect-all','zstandard',
        '--add-data',str(ROOT/'wx_watcher/assets')+':wx_watcher/assets',
        '--add-data',str(ROOT/'integrations/dsh')+':wx_watcher/assets/dsh',
        '--add-binary',sql+':bin',ROOT/'scripts/app_entry.py'])
    identity=os.environ.get('WX_CODESIGN_IDENTITY','-')
    artifacts=[]
    for name,keytool in [('WX Watcher',False),('WX Keytool',True)]:
        app=DIST/(name+'.app')
        if app.exists():shutil.rmtree(app)
        contents=app/'Contents';mac=contents/'MacOS';res=contents/'Resources'
        mac.mkdir(parents=True);res.mkdir()
        shutil.copy2(ROOT/'native/.build/release/wx-watcher-app',mac/'WXWatcher')
        shutil.copy2(ROOT/'native/.build/release/wx-keytool',res/'wx-keytool')
        shutil.copytree(BUILD/'python-dist/wx-watcher',res/'backend')
        # The standalone tool uses the same GUI and backend in keytool mode.
        info={'CFBundleName':name,'CFBundleDisplayName':name,'CFBundleIdentifier':'io.github.calwade.'+('wx-keytool' if keytool else 'wx-watcher'),
            'CFBundleExecutable':'WXWatcher','CFBundlePackageType':'APPL','CFBundleVersion':VERSION,
            'CFBundleShortVersionString':VERSION,'LSMinimumSystemVersion':'14.0','NSHighResolutionCapable':True,
            'WXKeytoolMode':keytool,'NSAppleEventsUsageDescription':'在你确认后使用系统授权窗口启动一次性的微信取钥与恢复任务。'}
        with (contents/'Info.plist').open('wb') as f:plistlib.dump(info,f)
        notices=res/'Notices';notices.mkdir()
        for file in ['LICENSE','THIRD_PARTY.md','native/Vendor/LICENSE.greenbubbles','wx_watcher/_vendor/wechat_decrypt/LICENSE']:
            source=ROOT/file;shutil.copy2(source,notices/(source.parent.name+'-'+source.name))
        for package in ['zstandard','pyinstaller','pyinstaller-hooks-contrib']:
            dist=importlib.metadata.distribution(package)
            for file in dist.files or []:
                if 'license' in str(file).lower() and dist.locate_file(file).is_file():
                    shutil.copy2(dist.locate_file(file),notices/(package+'-'+Path(str(file)).name))
        for prefix in ['/opt/homebrew/opt/sqlcipher','/opt/homebrew/opt/openssl@4','/opt/homebrew/opt/openssl@3']:
            for file in [Path(prefix)/'LICENSE',Path(prefix)/'LICENSE.txt',Path(prefix)/'LICENSE.md']:
                if file.is_file():shutil.copy2(file,notices/(Path(prefix).name+'-'+file.name))
        run(['/usr/bin/codesign','--force','--sign',identity,res/'wx-keytool'])
        run(['/usr/bin/codesign','--force','--deep','--sign',identity,app])
        run(['/usr/bin/codesign','--verify','--deep','--strict',app])
        archive=DIST/(name.replace(' ','-')+'-'+VERSION+'-arm64.zip')
        run(['/usr/bin/ditto','-c','-k','--keepParent',app,archive])
        artifacts.append(archive)
    stage=BUILD/'dmg';stage.mkdir(exist_ok=True)
    for name in ['WX Watcher.app','WX Keytool.app']:
        target=stage/name
        if target.exists():shutil.rmtree(target)
        shutil.copytree(DIST/name,target,symlinks=True)
    link=stage/'Applications'
    if not link.exists():link.symlink_to('/Applications')
    dmg=DIST/('WX-Watcher-'+VERSION+'-arm64.dmg')
    if dmg.exists():dmg.unlink()
    run(['/usr/bin/hdiutil','create','-volname','WX Watcher','-srcfolder',stage,'-ov','-format','UDZO',dmg])
    artifacts.append(dmg)
    (DIST/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in artifacts))
    manifest={'version':VERSION,'signing':'ad-hoc' if identity=='-' else 'Developer ID supplied',
        'notarized':False,'architecture':'arm64','artifacts':[p.name for p in artifacts]}
    (DIST/'build-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest))

if __name__=='__main__':main()
