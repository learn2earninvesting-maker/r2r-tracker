#!/usr/bin/env python3
"""
R2R Stocks X-ray — builds a results season from StockScans data (no CSV or PDF uploads).

Claude pulls the data through the StockScans connector and saves it in a work folder:
  numbers.json            {TICKER: {companyKey, n (name), s (industry), m: {metric: value}}}
                          — the same metric names the old coverage CSV had
  concall/<TICKER>.md     StockScans concall summary (markdown, as the connector returns it)
  catalysts/<TICKER>.md   StockScans growth catalysts report (markdown, numbered list)
  extras/<TICKER>.json    {result, promises, flags} — latest result, promises vs delivery, red-flag check
Anything not given is taken from --reuse (an earlier season folder), so a stock never loses
its concall summary or catalysts just because they were not refreshed this time.

Output is the same as build_season.py: research/seasons/<FOLDER>/{index.json, report.json,
stocks/<TICKER>.json}, plus research/staging.json, so the tracker bot's preview → Publish works
unchanged (send /research review to the bot after the push).

Text is copied as written: nothing is generated here.
"""
import argparse, datetime, json, os, re, shutil

def clean(t):
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', t)
    t = re.sub(r'\s*\[\d+(?:[-–,]\d+)*\]', '', t)          # [5-6] source markers
    return re.sub(r'\s+', ' ', t).strip()

def label_text(line):
    """'- **Label:** text'  →  {'label', 'text'}"""
    s = line.strip()[1:].strip()
    m = re.match(r'\*\*(.+?)\*\*\s*(.*)$', s)
    if m:
        lab = m.group(1).strip().rstrip(':').strip()
        return {'label': clean(lab), 'text': clean(m.group(2).lstrip(':').strip())}
    return {'label': '', 'text': clean(s)}

def parse_concall(md, name, sector):
    q = ''
    m = re.search(r'Concall Summary\s*[—-]\s*.+?,\s*(Q\d\s*FY\d\d)', md)
    if m: q = re.sub(r'\s+', ' ', m.group(1))
    blocks, cur = [], None
    for line in md.splitlines():
        if line.startswith('## '):
            title = line[3:].strip()
            if title.lower().startswith('concall summary'): continue
            cur = {'title': title, 'lines': []}; blocks.append(cur)
        elif cur is not None:
            cur['lines'].append(line)
    out = {'name': name, 'sector': sector, 'quarter': q, 'date': '', 'status': 'Latest quarter', 'takeaways': [], 'watch': None, 'sections': []}
    for b in blocks:
        pts, quotes, rows = [], [], []
        for l in b['lines']:
            s = l.strip()
            if s.startswith('- '): pts.append(label_text(s))
            elif s.startswith('>'):
                t = s.lstrip('>').strip()
                m2 = re.match(r'\*\*(.+?):\*\*\s*(.*)$', t)
                quotes.append({'who': clean(m2.group(1)) if m2 else '', 'text': clean(m2.group(2) if m2 else t).strip('"“” ')})
            elif s.startswith('|') and not re.match(r'^\|[\s|:-]+\|$', s):
                rows.append([clean(c) for c in s.strip('|').split('|')])
        if b['title'].lower() == 'key takeaways':
            for p in pts:
                if p['label'].lower().startswith('what to watch'): out['watch'] = {'label': 'What to watch next quarter', 'text': p['text']}
                else: out['takeaways'].append(p)
            continue
        if b['title'].lower() == 'key metrics': continue      # the Result tab shows the quarter's numbers
        sec = {'title': b['title'], 'points': pts}
        if rows: sec['table'] = {'cols': rows[0], 'rows': rows[1:]}
        if quotes: sec['quotes'] = quotes
        if pts or rows: out['sections'].append(sec)
    return out

def parse_catalysts(md, asof):
    items = []
    for line in md.splitlines():
        m = re.match(r'^\s*\d+\.\s+\*\*(.+?)\*\*\s*[—–-]\s*(.+)$', line)
        if m: items.append({'title': clean(m.group(1)), 'text': clean(m.group(2))})
    return {'asof': asof, 'items': items}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True, help='folder with numbers.json, concall/, catalysts/')
    ap.add_argument('--reuse', help='earlier season folder (research/seasons/<folder>) for text not refreshed')
    ap.add_argument('--season', required=True, help='e.g. Q2FY27')
    ap.add_argument('--out', default='research/seasons')
    ap.add_argument('--build', default=datetime.datetime.now(datetime.timezone.utc).strftime('%y%m%d%H%M') + '-ss')
    ap.add_argument('--staging', default='research/staging.json')
    a = ap.parse_args()
    season = a.season.replace(' ', '')
    label = re.sub(r'^(Q\d)(FY\d\d)$', r'\1 \2', season)
    nums = json.load(open(os.path.join(a.work, 'numbers.json')))
    old = json.load(open(os.path.join(a.reuse, 'index.json')))['stocks'] if a.reuse else {}
    folder = f'{season}-{a.build}'
    outdir = os.path.join(a.out, folder)
    if os.path.exists(outdir): shutil.rmtree(outdir)
    os.makedirs(os.path.join(outdir, 'stocks'))
    now = datetime.datetime.now(datetime.timezone.utc)
    asof = now.strftime('%b %Y')
    idx, rep = {}, {'season': season, 'label': label, 'folder': folder, 'built': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'source': 'StockScans',
                    'csvStocks': len(nums), 'concall': 0, 'concallLatest': 0, 'catalysts': 0, 'noConcall': [], 'olderConcall': [], 'noCatalysts': [],
                    'refreshed': {'concall': [], 'catalysts': []}, 'unmatched': {'concall': [], 'catalysts': []}}
    for t in sorted(nums):
        v = nums[t]
        det = {'ticker': t, 'name': v['n'], 'concall': None, 'catalysts': None}
        prev = None
        if a.reuse and os.path.exists(os.path.join(a.reuse, 'stocks', t + '.json')):
            prev = json.load(open(os.path.join(a.reuse, 'stocks', t + '.json')))
        cp = os.path.join(a.work, 'concall', t + '.md')
        if os.path.exists(cp):
            det['concall'] = parse_concall(open(cp).read(), v['n'], v['s']); rep['refreshed']['concall'].append(t)
        elif prev: det['concall'] = prev.get('concall')
        kp = os.path.join(a.work, 'catalysts', t + '.md')
        if os.path.exists(kp):
            det['catalysts'] = parse_catalysts(open(kp).read(), asof); rep['refreshed']['catalysts'].append(t)
        elif prev: det['catalysts'] = prev.get('catalysts')
        xp = os.path.join(a.work, 'extras', t + '.json')
        if os.path.exists(xp):
            x = json.load(open(xp)); det['extras'] = {k: x.get(k) for k in ('asOf', 'result', 'promises', 'flags') if x.get(k)}; rep.setdefault('extras', []).append(t)
        elif prev and prev.get('extras'): det['extras'] = prev['extras']
        e = {'n': v['n'], 's': v['s'], 'm': v['m']}
        if det.get('extras'): e['x'] = 1
        c = det['concall']
        if c and (c.get('takeaways') or c.get('sections')):
            latest = (c.get('quarter') or '').replace(' ', '') == season
            e['cc'] = {'q': c.get('quarter') or '', 'd': c.get('date') or '', 'latest': latest}
            rep['concall'] += 1; rep['concallLatest'] += latest
            if not latest: rep['olderConcall'].append(f"{v['n']} ({c.get('quarter') or '?'})")
        else: rep['noConcall'].append(v['n'])
        g = det['catalysts']
        if g and g.get('items'):
            e['cat'] = len(g['items']); e['catAsOf'] = g.get('asof') or (old.get(t) or {}).get('catAsOf') or asof; rep['catalysts'] += 1
        else: rep['noCatalysts'].append(v['n'])
        idx[t] = e
        if det['concall'] or det['catalysts'] or det.get('extras'):
            json.dump(det, open(os.path.join(outdir, 'stocks', t + '.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump({'season': season, 'label': label, 'folder': folder, 'built': rep['built'], 'stocks': idx}, open(os.path.join(outdir, 'index.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    json.dump(rep, open(os.path.join(outdir, 'report.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump({'build': a.build, 'folder': folder, 'season': season, 'label': label, 'built': rep['built'], 'report': rep}, open(a.staging, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
