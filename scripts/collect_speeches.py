#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""제주특별자치도의회 전자회의록 → 의원별 발언 수집기"""
import re, json, time, argparse, sys, os, urllib.request
from datetime import datetime, timezone, timedelta

BASE = "https://record.council.jeju.kr"
LIST = BASE + "/source/minutes/pages/period.html"
VIEW = BASE + "/CLRecords/Retrieval2/index.php"
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")}
KST = timezone(timedelta(hours=9))

def fetch(url, tries=3, pause=1.2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
            time.sleep(pause)
            return raw.decode("euc-kr", "replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"fetch 실패: {url}\n{last}")

TAG = re.compile(r"<[^>]+>")
WS  = re.compile(r"[ \t\r\f\v]+")
NL  = re.compile(r"\n{3,}")
ENT = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}

def text_of(html):
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", h)
    h = TAG.sub("", h)
    for k, v in ENT.items():
        h = h.replace(k, v)
    h = WS.sub(" ", h)
    h = NL.sub("\n\n", h)
    return "\n".join(l.strip() for l in h.split("\n")).strip()

RE_TH    = re.compile(r"period\.html\?daesu=(\d+)&(?:amp;)?th=(\d+)")
RE_HFILE = re.compile(r"hfile=([0-9A-Za-z]+\.html)")
RE_SUB   = re.compile(r"period\.html\?daesu=(\d+)&(?:amp;)?th=(\d+)&(?:amp;)?fcode=([A-Z0-9]+)&(?:amp;)?cha=(\d+)")

def sessions(daesu):
    html = fetch(f"{LIST}?daesu={daesu}&tag=lth")
    return sorted({int(t) for d, t in RE_TH.findall(html) if int(d) == daesu})

def meetings(daesu, th):
    page = fetch(f"{LIST}?daesu={daesu}&th={th}&fchk=A&tag=lth")
    found = set(RE_HFILE.findall(page))
    subs = {(f, c) for d, t, f, c in RE_SUB.findall(page)
            if int(d) == daesu and int(t) == th}
    for fcode, cha in sorted(subs):
        try:
            sp = fetch(f"{LIST}?daesu={daesu}&th={th}&fcode={fcode}&cha={cha}&fchk=A&tag=langun")
            found |= set(RE_HFILE.findall(sp))
        except Exception as e:
            print(f"  ! 하위목록 실패 {fcode}/{cha}: {e}", file=sys.stderr)
    return sorted(found)

RE_SPK   = re.compile(r'<a[^>]+profile\.php\?f_code=(\d+)[^>]*>(.*?)</a>', re.S | re.I)
RE_ANGUN = re.compile(r'<a[^>]+name=["\']angun(\d+)["\']', re.I)
RE_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
RE_DATE  = re.compile(r"(\d{4})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})")
SUFFIX   = re.compile(r"^\s*(부위원장|위원장|의원|위원|의장)\b[\s:·]*")
TAIL     = re.compile(r"[◯○●][^◯○●]{0,25}$")
RE_MARK  = re.compile(r"[◯○●]")

def clean_body(body, role):
    m = SUFFIX.match(body)
    if m:
        if not role:
            role = m.group(1)
        body = body[m.end():]
    body = TAIL.sub("", body)
    return body.strip(), role

def parse(html, hfile):
    title = text_of(RE_TITLE.search(html).group(1)) if RE_TITLE.search(html) else ""
    m = RE_DATE.search(title)
    date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
    agendas = [(mm.start(), int(mm.group(1))) for mm in RE_ANGUN.finditer(html)]
    def agenda_at(pos):
        cur = 0
        for p, n in agendas:
            if p <= pos: cur = n
            else: break
        return cur
    marks = [m.start() for m in RE_MARK.finditer(html)]
    out, others = [], 0
    for i, st in enumerate(marks):
        en = marks[i + 1] if i + 1 < len(marks) else len(html)
        chunk = html[st:en]
        sp = RE_SPK.search(chunk[:400])
        if not sp:
            others += 1
            continue
        head = text_of(chunk[:sp.start()]).lstrip("◯○● ").strip()
        body, role = clean_body(text_of(chunk[sp.end():]), head)
        out.append({
            "hfile": hfile, "f_code": sp.group(1), "name": text_of(sp.group(2)),
            "role": role, "agenda_no": agenda_at(st), "chars": len(body), "text": body,
            "url": f"{VIEW}?hfile={hfile}&daesu={hfile[:2]}#angun{agenda_at(st)}",
        })
    return {"hfile": hfile, "title": title, "date": date,
            "url": f"{VIEW}?hfile={hfile}&daesu={hfile[:2]}",
            "turns_total": len(marks), "turns_nonmember": others, "speeches": out}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daesu", type=int, default=13)
    ap.add_argument("--th", type=int, default=None)
    ap.add_argument("--out", default="speeches.json")
    ap.add_argument("--min-chars", type=int, default=20)
    a = ap.parse_args()

    ths = [a.th] if a.th else sessions(a.daesu)
    print(f"제{a.daesu}대 회기: {ths}")
    docs, seen = [], set()
    for th in ths:
        hs = meetings(a.daesu, th)
        print(f"  제{th}회 → 회의록 {len(hs)}건")
        for hf in hs:
            if hf in seen: continue
            seen.add(hf)
            try:
                docs.append(parse(fetch(f"{VIEW}?hfile={hf}&daesu={a.daesu}"), hf))
            except Exception as e:
                print(f"    ! {hf}: {e}", file=sys.stderr)

    speeches = [s for d in docs for s in d["speeches"] if s["chars"] >= a.min_chars]
    by = {}
    for s in speeches:
        b = by.setdefault(s["f_code"], {"f_code": s["f_code"], "names": {}, "count": 0,
                                        "chars": 0, "meetings": set()})
        b["names"][s["name"]] = b["names"].get(s["name"], 0) + 1
        b["count"] += 1; b["chars"] += s["chars"]; b["meetings"].add(s["hfile"])
    for b in by.values():
        b["name"] = max(b["names"], key=b["names"].get)
        b["meetings"] = len(b["meetings"]); del b["names"]

    res = {"generated": datetime.now(KST).isoformat(timespec="seconds"), "daesu": a.daesu,
           "meetings": [{k: v for k, v in d.items() if k != "speeches"} for d in docs],
           "speakers": sorted(by.values(), key=lambda x: -x["count"]), "speeches": speeches}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n회의록 {len(docs)}건 / 발언 {len(speeches)}건 / 발언자 {len(by)}명 → {a.out}")
    print("\n발언 많은 순 상위 20")
    for b in res["speakers"][:20]:
        print(f"  {b['name']:<8} f_code={b['f_code']:>5}  {b['count']:>4}회  {b['chars']:>7}자  회의 {b['meetings']}건")

if __name__ == "__main__":
    main()
