"""Delete the volcano SVGs that the page does not use (2026-10-23: the names carry a data hash, so each data change
leaves the old files). The local folder; with --s3 also the published copies: S3 (<PUBLISH_S3_PREFIX>volcanoes_px/)
and the box web folder. Dry run unless --apply. Prints counts only.
Usage: python python/prune_volcanoes.py output/interfaces [--s3] [--apply] [--config config/config.yaml]
Stops if the page is older than python/functions.py (built before the data-keyed names), if the page uses a file
that the local folder or S3 lacks, or if S3 or the box holds another data.js (the published page is not this one)."""
import argparse, os, re, sys

ap = argparse.ArgumentParser(description='Delete the volcano SVGs that the page does not use.')
ap.add_argument('interfaces', help='the build folder that holds the page, e.g. output/interfaces')
ap.add_argument('--s3', action='store_true', help='also the published copies: S3 and the box (the publish targets)')
ap.add_argument('--apply', action='store_true', help='delete; without it, a dry run')
ap.add_argument('--config', default='config/config.yaml', help='the YAML config (PUBLISH_* keys), for --s3')
args = ap.parse_args()
d = os.path.abspath(args.interfaces)
cfg = os.path.abspath(args.config)
repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# the files that the page uses
vdir = os.path.join(d, 'volcanoes_px')
page = [os.path.join(d, f) for f in ('Serac_Px_interface.html', 'Serac_Px_interface_data.js')]
if min(os.path.getmtime(f) for f in page) < os.path.getmtime(os.path.join(repo, 'python', 'functions.py')):
    sys.exit('stop: the page is older than python/functions.py; run the new build first')
used = set()
for f in page:
    used |= set(re.findall(r'[0-9a-f]{16}\.svg', open(f, encoding='utf-8').read()))

# 1. the local folder
have = {f for f in os.listdir(vdir) if f.endswith('.svg')}
missing, old = used - have, sorted(have - used)
print(f'local: page uses {len(used):,} svg | folder has {len(have):,} | used but missing {len(missing):,} | '
      f'not used {len(old):,} ({sum(os.path.getsize(os.path.join(vdir, f)) for f in old) / 1e6:.0f} MB)')
if missing:
    sys.exit('stop: the page uses files that the folder lacks; check the build first')
if args.apply:
    for f in old:
        os.remove(os.path.join(vdir, f))
    print(f'local: deleted {len(old):,} files; {len(have) - len(old):,} remain')
if not args.s3:
    if not args.apply:
        print('dry run: add --apply to delete')
    sys.exit()

# 2. S3, then 3. the box (it copies from S3, so a --delete sync of volcanoes_px removes the same files)
sys.path.insert(0, repo)
import python.Px_interface as px
p = px.PARAMS(cfg).load_params()
session, bucket, iid = px.aws_publish_targets(p)
s3, prefix, web = session.client('s3'), p.PUBLISH_S3_PREFIX, p.PUBLISH_WEBROOT
size = os.path.getsize(page[1])
key = prefix + os.path.basename(page[1])   # the size from a listing: the bucket allows no object read outside the VPC
if next((o['Size'] for o in s3.list_objects_v2(Bucket=bucket, Prefix=key).get('Contents', []) if o['Key'] == key), None) != size:
    sys.exit('stop: S3 holds another data.js; publish this page first')
inv = px.ssm_run(session, iid, ['set -e', f'stat -c %s {web}{os.path.basename(page[1])}',
                                f'ls {web}volcanoes_px | grep -c "\\.svg$" || true'])
out = inv['StandardOutputContent'].split()
if inv['Status'] != 'Success' or len(out) < 2:
    sys.exit(f"stop: cannot read the box folder ({inv['Status']})")
box_size, box_n = int(out[0]), int(out[1])
if box_size != size:
    sys.exit('stop: the box holds another data.js; publish this page first')
keys = [o['Key'] for pg in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix + 'volcanoes_px/')
        for o in pg.get('Contents', []) if o['Key'].endswith('.svg')]
names = {k.rsplit('/', 1)[1] for k in keys}
s3_old = sorted(k for k in keys if k.rsplit('/', 1)[1] not in used)
print(f'S3: {len(names):,} svg | used but missing {len(used - names):,} | not used {len(s3_old):,}')
print(f'box: {box_n:,} svg | not used about {box_n - len(used):,}')
if used - names:
    sys.exit('stop: S3 lacks files that the page uses; publish this page first')
if not args.apply:
    print('dry run: add --apply to delete')
    sys.exit()
errors = 0
for i in range(0, len(s3_old), 1000):
    r = s3.delete_objects(Bucket=bucket, Delete={'Objects': [{'Key': k} for k in s3_old[i:i + 1000]], 'Quiet': True})
    errors += len(r.get('Errors', []))
print(f'S3: deleted {len(s3_old) - errors:,} files ({errors} errors)')
if errors:
    sys.exit('stop: S3 delete errors; the box is not changed')
inv = px.ssm_run(session, iid, [
    f"aws s3 sync s3://{bucket}/{prefix}volcanoes_px/ {web}volcanoes_px/ --delete --exclude '*.json' "
    f'--region {p.PUBLISH_REGION} --only-show-errors', f'ls {web}volcanoes_px | grep -c "\\.svg$" || true'])
if inv['Status'] != 'Success':
    sys.exit(f"stop: the box sync ended {inv['Status']}: {inv['StandardErrorContent'][-300:]}")
print(f"box: {int(inv['StandardOutputContent'].split()[0]):,} svg remain")
