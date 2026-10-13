"""Build a clean plugin ZIP using standard Python; uploads nothing."""
import argparse
import ast
import configparser
import hashlib
import json
from pathlib import Path
import re
import zipfile

root=Path(__file__).resolve().parents[1]
package=root/'plugin/draw_catchments'
parser=argparse.ArgumentParser()
parser.add_argument('--local',action='store_true',help='Allow incomplete publisher metadata for local testing')
args=parser.parse_args()
config=configparser.ConfigParser();config.read(package/'metadata.txt',encoding='utf-8')
info=config['general']
for key in ('name','description','about','version','author','qgisMinimumVersion','license'):
    if not info.get(key,'').strip():raise SystemExit('Missing metadata: '+key)
if not re.fullmatch(r'\d+\.\d+\.\d+',info['version']):raise SystemExit('Expected a numeric major.minor.patch version')
missing=[key for key in ('email','homepage','repository','tracker') if not info.get(key,'').strip()]
if info.get('email') and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',info['email']):raise SystemExit('Invalid email')
for key in ('homepage','repository','tracker'):
    if info.get(key) and not info[key].startswith('https://'):raise SystemExit('Use HTTPS for '+key)
if missing and not args.local:raise SystemExit('Complete publisher fields first: '+', '.join(missing))
for required in ('__init__.py','plugin.py','metadata.txt','LICENSE','README.md','icon.png','examples/synthetic_plane.gpkg'):
    if not (package/required).is_file():raise SystemExit('Missing file: '+required)
files=[p for p in sorted(package.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo') and p.relative_to(package).parts[0]!='docs']
for path in files:
    if path.suffix=='.py':ast.parse(path.read_text(encoding='utf-8'),filename=str(path.relative_to(root)))
    if path.suffix.lower() in ('.exe','.dll','.so','.dylib'):raise SystemExit('Compiled binary is not allowed: '+path.name)
out=root/'dist';out.mkdir(exist_ok=True);(root/'outputs').mkdir(exist_ok=True)
zip_path=out/('Draw_Catchments-'+info['version']+'.zip')
with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in files:archive.write(path,'draw_catchments/'+path.relative_to(package).as_posix())
with zipfile.ZipFile(zip_path) as archive:
    assert archive.testzip() is None
    assert all(n.startswith('draw_catchments/') and '..' not in Path(n).parts for n in archive.namelist())
if zip_path.stat().st_size>=25*1024*1024:raise SystemExit('Plugin ZIP exceeds 25 MB')
digest=hashlib.sha256(zip_path.read_bytes()).hexdigest()
(out/'SHA256SUMS.txt').write_text(digest+'  '+zip_path.name+'\n',encoding='utf-8')
report={'zip':zip_path.name,'bytes':zip_path.stat().st_size,'sha256':digest,
        'metadata_complete':not missing,'missing_fields':missing,
        'runtime_tested_by_this_script':False,'public_links_checked_by_this_script':False}
(out/'build_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
