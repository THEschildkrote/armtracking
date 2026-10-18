#!/usr/bin/env python3
"""Stream CASAS activity-start aggregates from the supplied ZIP; no raw extraction."""
import argparse,collections,csv,hashlib,io,json,re,zipfile
from pathlib import Path

def build(archive):
    result={'schema_version':1,'source':'User-supplied CASAS labeled_data.zip','source_url':'https://casas.wsu.edu/datasets/',
            'method':'Count annotated activity BEGIN events, not sensor activations; retain per-recording hourly counts.', 'recordings':{}}
    digest=hashlib.sha256()
    with open(archive,'rb') as stream:
        for b in iter(lambda:stream.read(1024*1024),b''):digest.update(b)
    result['archive_sha256']=digest.hexdigest()
    with zipfile.ZipFile(archive) as z:
        seen={};result['duplicate_files']=[]
        for name in sorted(z.namelist()):
            if not name.endswith(('.csv','.txt')):continue
            info=z.getinfo(name);signature=(info.file_size,info.CRC)
            if signature in seen:
                def digest_member(n):
                    h=hashlib.sha256()
                    with z.open(n) as f:
                        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
                    return h.hexdigest()
                if digest_member(name)==digest_member(seen[signature]):
                    result['duplicate_files'].append({'excluded':name,'same_as':seen[signature]});continue
            seen[signature]=name
            hours=[collections.Counter() for _ in range(24)];n=0;bad=0;first=None;last=None;ends=0
            with z.open(name) as f:
                for row in csv.reader(io.TextIOWrapper(f,encoding='utf-8-sig',errors='replace')):
                    n+=1
                    if len(row)<4:bad+=1;continue
                    stamp=row[0]+' '+row[1]
                    first=stamp if first is None else min(first,stamp);last=stamp if last is None else max(last,stamp)
                    try:hour=int(row[1].split(':')[0]);assert 0<=hour<24
                    except (ValueError,AssertionError):bad+=1;continue
                    for label in row[4:]:
                        m=re.fullmatch(r'(.+?)\s*=\s*["\']?(begin|end)["\']?',label.strip(),re.I)
                        if m:
                            if m[2].lower()=='begin':hours[hour][m[1].strip()]+=1
                            else:ends+=1
            if Path(name).stem in result['recordings']:raise ValueError('Conflicting recording IDs in archive')
            result['recordings'][Path(name).stem]={'rows':n,'malformed_rows':bad,'first_timestamp':first,'last_timestamp':last,'end_annotations':ends,'hours':list(map(dict,hours))}
    result['sensor_rows']=sum(r['rows'] for r in result['recordings'].values())
    totals=collections.Counter()
    for r in result['recordings'].values():
        for h in r['hours']:totals.update(h)
    result['activity_start_counts']=dict(totals.most_common())
    result['activity_starts']=sum(totals.values())
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('archive');p.add_argument('--out',default='reports/casas_activity_counts.json');a=p.parse_args()
    d=build(a.archive);out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(d,indent=2))
    print(json.dumps({k:v for k,v in d.items() if k!='recordings'},indent=2))
