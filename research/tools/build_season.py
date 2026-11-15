#!/usr/bin/env python3
"""
R2R Stocks X-ray — builds one results season's research files.

Inputs (any file can be missing):
  --csv        Tickertape export (first column companyId, e.g. NSE:DIVISLAB)
  --concall    StockScans "Transcript Summary" PDF
  --catalysts  StockScans "Growth Catalysts" PDF
Output:
  research/seasons/<SEASON>/index.json         every stock: name, sector, numbers, what reports exist
  research/seasons/<SEASON>/stocks/<TICK>.json  concall summary + growth catalysts, word for word
  research/seasons/<SEASON>/report.json        what was found / missing / unmatched (for the bot preview)

Parsing is rule-based on the PDFs' fonts and layout — text is copied as written, nothing is generated.
"""
import argparse, csv, datetime, json, os, re, sys

def num(v):
    v = (v or '').strip()
    if v in ('', 'null', 'NaN', '-'): return None
    try: return float(v.replace(',', ''))
    except ValueError: return v

def key(name):
    s = (name or '').lower().replace('&', ' and ')
    s = re.sub(r'\b(ltd|limited|the|india|co|company|corp|corporation|of|and)\b', ' ', s)
    return re.sub(r'[^a-z0-9]', '', s)

# ───────────────────────── PDF lines ─────────────────────────
def page_lines(page):
    rows = {}
    for c in page.chars:
        rows.setdefault(round(c['top']), []).append(c)
    out = []
    for top in sorted(rows):
        cs = sorted(rows[top], key=lambda c: c['x0'])
        txt, prev = '', None
        segs = []   # (text, semibold?)
        for c in cs:
            if prev is not None and c['x0'] - prev['x1'] > c['size'] * 0.22 and not txt.endswith(' ') and c['text'] != ' ':
                txt += ' '; segs.append((' ', False))
            txt += c['text']; segs.append((c['text'], 'SemiBold' in c['fontname'] or 'Bold' in c['fontname']))
            prev = c
        f = cs[0]
        out.append({'top': top, 'x': round(f['x0']), 'size': round(max(c['size'] for c in cs), 1),
                    'font': f['fontname'].split('+')[-1], 'text': txt.strip(), 'segs': segs, 'chars': cs})
    return out

def lead_bold(segs, strip_prefix):
    """Leading semibold run (the bullet label / catalyst title) and the rest."""
    s = ''.join(t for t, _ in segs)
    i = 0
    # skip the bullet / number prefix
    m = re.match(strip_prefix, s)
    start = m.end() if m else 0
    j = start
    while j < len(segs) and (segs[j][1] or segs[j][0] == ' '): j += 1
    bold = ''.join(t for t, _ in segs[start:j]).strip()
    rest = ''.join(t for t, _ in segs[j:]).strip()
    return bold, rest

def company_pages(path):
    import pdfplumber
    pdf = pdfplumber.open(path)
    cur = None
    for p in pdf.pages:
        L = page_lines(p)
        if not L: continue
        head = L[0]
        if not (head['font'].endswith('InterBold') or 'Bold' in head['font']) or head['top'] > 30 or head['size'] < 10.5: continue
        if len(L) < 3 or not re.search(r'(Transcript Summary|Growth Catalysts) ·', L[2]['text'] + ' ' + (L[3]['text'] if len(L) > 3 else '')):
            continue
        name = head['text']
        hdr = [l for l in L if l['top'] < 70]
        body = [l for l in L if 70 <= l['top'] < 800 and not ('Italic' in l['font'] and l['size'] <= 7.5)]
        if cur is None or cur['name'] != name:
            if cur: yield cur
            cur = {'name': name, 'header': [h['text'] for h in hdr], 'lines': []}
        cur['lines'] += body
    if cur: yield cur

def cells(line, bounds):
    """Split a table line's characters into columns at the header's x positions."""
    out = [''] * len(bounds)
    prev = [None] * len(bounds)
    for c in line['chars']:
        i = max([k for k, b in enumerate(bounds) if c['x0'] >= b - 3] or [0])
        p = prev[i]
        if p is not None and c['x0'] - p['x1'] > c['size'] * 0.22 and not out[i].endswith(' ') and c['text'] != ' ': out[i] += ' '
        out[i] += c['text']; prev[i] = c
    return [o.strip() for o in out]

def join_text(a, b):
    if a.endswith('-') and not a.endswith(' -'): return a + b
    return (a + ' ' + b).strip()

def parse_concall(path):
    out = {}
    for c in company_pages(path):
        info = {'name': c['name'], 'sector': c['header'][1] if len(c['header']) > 1 else '', 'quarter': '', 'date': '', 'status': '',
                'takeaways': [], 'watch': '', 'sections': []}
        m = re.search(r'Transcript Summary · (.+)$', ' '.join(c['header']))
        if m: info['date'] = m.group(1).replace('www.stockscans.in', '').strip()
        sec, item, quote = None, None, None
        for l in c['lines']:
            t = l['text']
            if l['size'] >= 12.5:                                  # title: "<Name>: Q2 FY27 Transcript Summary"
                m = re.search(r'(Q[1-4]\s*FY\d{2})', t); info['quarter'] = m.group(1).replace(' ', ' ') if m else ''
                continue
            if l['size'] <= 8 and 'SemiBold' in l['font'] and not info['status']:
                info['status'] = t; continue                        # "Latest quarter"
            if 'SemiBold' in l['font'] and 10.5 <= l['size'] < 12.5 and l['x'] < 38:   # section heading
                sec = {'title': t, 'points': [], 'quotes': []}; item = quote = None
                if t.lower() != 'key takeaways': info['sections'].append(sec)
                else: sec['_kt'] = True
                continue
            if 'Bold' in l['font'] and 'Semi' not in l['font'] and l['size'] <= 8.8 and t.lower().startswith('important notice'):
                sec = None; continue
            if sec is None: continue
            allbold = all('Bold' in ch['fontname'] and 'Semi' not in ch['fontname'] for ch in l['chars'] if ch['text'].strip())
            if allbold and 8.5 <= l['size'] <= 9.2 and l['x'] < 38 and 'table' not in sec:   # table header row
                xs, last = [], None
                for ch in l['chars']:
                    if last is None or ch['x0'] - last['x1'] > 12: xs.append(ch['x0'])
                    last = ch
                sec['table'] = {'bounds': xs, 'cols': cells(l, xs), 'rows': []}; sec['_last'] = l['top']; continue
            if 'table' in sec and l['size'] <= 9.2 and not (40 <= l['x'] <= 60):
                row = cells(l, sec['table']['bounds'])
                rows = sec['table']['rows']
                if row == sec['table']['cols']: sec['_last'] = l['top']; continue     # header repeated on the next page
                if rows and l['top'] - sec['_last'] < 18:          # wrapped line inside the same row
                    for k, v in enumerate(row):
                        if v: rows[-1][k] = join_text(rows[-1][k], v)
                else: rows.append(row)
                sec['_last'] = l['top']; continue
            if t.startswith('•'):
                label, rest = lead_bold(l['segs'], r'\s*•\s*')
                item = {'label': label.rstrip(':').strip(), 'text': rest}; quote = None
                if sec.get('_kt'):
                    if item['label'].lower().startswith('what to watch'): info['watch'] = item; item['_w'] = True
                    else: info['takeaways'].append(item)
                else: sec['points'].append(item)
                continue
            if l['size'] <= 9.2 and l['x'] >= 38:                   # management quote (smaller type, indented)
                if quote is None or l['top'] - quote['_bottom'] > 22:
                    quote = {'text': t, '_bottom': l['top']}; sec['quotes'].append(quote); item = None
                else: quote['text'] = join_text(quote['text'], t); quote['_bottom'] = l['top']
                continue
            if item is not None and l['x'] >= 38:                  # bullet continuation
                item['text'] = join_text(item['text'], t); continue
        for s in info['sections']:
            for q in s['quotes']: q.pop('_bottom', None)
            s.pop('_last', None)
            if 'table' in s: s['table'].pop('bounds', None)
        info['sections'] = [s for s in info['sections'] if s['points'] or s['quotes'] or s.get('table')]
        if isinstance(info['watch'], dict): info['watch'].pop('_w', None)
        out[c['name']] = info
    return out

def parse_catalysts(path):
    out = {}
    for c in company_pages(path):
        info = {'name': c['name'], 'sector': '', 'asof': '', 'items': []}
        h = ' '.join(c['header'])
        m = re.search(r'as of ([A-Za-z]+ \d{4})', h); info['asof'] = m.group(1) if m else ''
        if len(c['header']) > 1: info['sector'] = c['header'][1].split(' · M.Cap')[0]
        item = None
        for l in c['lines']:
            t = l['text']
            if 'Bold' in l['font'] and l['size'] <= 9 and t.lower().startswith('important notice'): break
            if l['size'] < 9: continue
            if re.match(r'^\d{1,2}\.', t) and l['x'] < 40:
                title, rest = lead_bold(l['segs'], r'\s*\d{1,2}\.\s*')
                title = title.rstrip(' -–').strip()
                rest = re.sub(r'^[-–]\s*', '', rest)
                item = {'title': title, 'text': rest}; info['items'].append(item); continue
            if item is not None and l['x'] >= 40: item['text'] = join_text(item['text'], t)
        out[c['name']] = info
    return out

# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv'); ap.add_argument('--concall'); ap.add_argument('--catalysts')
    ap.add_argument('--season', help='e.g. Q2FY27 (default: the most common quarter in the concall PDF)')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(__file__), '..', 'seasons'))
    ap.add_argument('--reuse', help='index.json of an earlier season: its numbers are used when no CSV is given')
    ap.add_argument('--folder', help='output folder name under --out (default: the season)')
    ap.add_argument('--aliases', default=os.path.join(os.path.dirname(__file__), '..', 'aliases.json'))
    a = ap.parse_args()

    aliases = {}
    if os.path.exists(a.aliases):
        aliases = {key(k): v for k, v in json.load(open(a.aliases)).items() if not k.startswith('_')}

    stocks, by_key = {}, {}
    if a.csv:
        with open(a.csv, newline='', encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                cid = (r.get('companyId') or '').strip()
                if not cid: continue
                tick = cid.split(':')[-1].strip().upper()
                if not r.get('Industry') or r.get('Industry') == 'null': continue   # ETFs etc. — not analysed
                stocks[tick] = {'name': r['Name'].strip(), 'sector': r['Industry'].strip(),
                                'm': {k: num(v) for k, v in r.items() if k not in ('companyId', 'Name', 'Industry')}}
                by_key[key(r['Name'])] = tick

    if not stocks and a.reuse and os.path.exists(a.reuse):
        for tick, v in json.load(open(a.reuse)).get('stocks', {}).items():
            stocks[tick] = {'name': v['n'], 'sector': v['s'], 'm': v['m']}
            by_key[key(v['n'])] = tick

    def match(name):
        k = key(name)
        if k in aliases: return aliases[k]
        if k in by_key: return by_key[k]
        for kk, t in by_key.items():                 # prefix match as a fallback (e.g. "Divis Laboratories" vs "Divi's Labs")
            if len(k) > 6 and (kk.startswith(k) or k.startswith(kk)): return t
        return None

    cc = parse_concall(a.concall) if a.concall else {}
    ct = parse_catalysts(a.catalysts) if a.catalysts else {}

    season = a.season
    if not season:
        qs = [v['quarter'] for v in cc.values() if v['quarter']]
        season = max(set(qs), key=qs.count).replace(' ', '') if qs else datetime.date.today().strftime('S%Y%m')
    label = re.sub(r'^(Q\d)(FY\d+)$', r'\1 \2', season)

    unmatched = {'concall': [], 'catalysts': []}
    detail = {}
    for src, d in (('concall', cc), ('catalysts', ct)):
        for name, info in d.items():
            t = match(name)
            if not t:
                unmatched[src].append(name)
                if not stocks: t = re.sub(r'[^A-Z0-9]', '', name.upper())[:20]
                else: continue
            detail.setdefault(t, {})[src] = info

    folder = a.folder or season
    outdir = os.path.join(a.out, folder)
    os.makedirs(os.path.join(outdir, 'stocks'), exist_ok=True)
    idx = {}
    for t, s in stocks.items():
        d = detail.get(t, {})
        c = d.get('concall'); g = d.get('catalysts')
        idx[t] = {'n': s['name'], 's': s['sector'], 'm': s['m'],
                  'cc': ({'q': c['quarter'], 'd': c['date'], 'latest': c['quarter'].replace(' ', '') == season} if c else None),
                  'cat': len(g['items']) if g else 0, 'catAsOf': g['asof'] if g else ''}
        if d:
            json.dump({'ticker': t, 'name': s['name'], 'concall': c, 'catalysts': g}, open(os.path.join(outdir, 'stocks', t + '.json'), 'w'), ensure_ascii=False)
    built = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    json.dump({'season': season, 'label': label, 'folder': folder, 'built': built, 'stocks': idx}, open(os.path.join(outdir, 'index.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    rep = {'season': season, 'label': label, 'folder': folder, 'built': built, 'csvStocks': len(stocks),
           'concall': sum(1 for v in idx.values() if v['cc']), 'concallLatest': sum(1 for v in idx.values() if v['cc'] and v['cc']['latest']),
           'catalysts': sum(1 for v in idx.values() if v['cat']),
           'noConcall': sorted(v['n'] for v in idx.values() if not v['cc']),
           'olderConcall': sorted(f"{v['n']} ({v['cc']['q']})" for v in idx.values() if v['cc'] and not v['cc']['latest']),
           'noCatalysts': sorted(v['n'] for v in idx.values() if not v['cat']),
           'unmatched': unmatched}
    json.dump(rep, open(os.path.join(outdir, 'report.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
