"""Set the public maintainer email; run before building the submission ZIP."""
import argparse
from pathlib import Path
import re
parser=argparse.ArgumentParser()
parser.add_argument('email',help='Public maintainer contact address')
args=parser.parse_args()
if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',args.email):
    parser.error('Enter a valid email address.')
path=Path(__file__).resolve().parents[1]/'plugin/draw_catchments/metadata.txt'
text=path.read_text(encoding='utf-8')
text=re.sub(r'^email=.*$',lambda _: 'email='+args.email,text,flags=re.M)
path.write_text(text,encoding='utf-8')
print('Maintainer email updated. Rebuild the ZIP and commit this metadata change.')
